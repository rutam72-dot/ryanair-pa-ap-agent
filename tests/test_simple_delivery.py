import copy
import io
import json
import tempfile
import unittest
from datetime import date, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from calendar_sync import CalendarError, GoogleCalendarApi, deliver
from monitor import (CALENDAR, MARKER, load_state, save_state, prepare, health_errors,
                     observation_text)
from test_simple_monitor import DAY, KEY, NOW, plan, seeded, empty_state, legacy_increase_plan


class FakeApi:
    def __init__(self):
        self.events = {}
        self.inserts = 0
        self.patches = 0
        self.deleted = []
        self.fail_readback = False
        self.fail_insert_after_write = False
        self.fail_delete = False

    def list(self, *args):
        return [copy.deepcopy(e) for e in self.events.values() if e.get("status") != "cancelled"]

    def get(self, identifier):
        if self.fail_readback and identifier in self.events:
            raise CalendarError("Readback unavailable")
        return copy.deepcopy(self.events.get(identifier))

    def insert(self, identifier, body):
        if identifier in self.events:
            raise CalendarError("Conflict", 409)
        self.inserts += 1
        self.events[identifier] = {**copy.deepcopy(body), "id": identifier, "status": "confirmed"}
        if self.fail_insert_after_write:
            self.fail_insert_after_write = False
            raise CalendarError("Response lost")

    def patch(self, identifier, body):
        self.patches += 1
        self.events[identifier].update(copy.deepcopy(body))

    def delete(self, identifier):
        if self.fail_delete:
            raise CalendarError("Delete unavailable")
        self.deleted.append(identifier)
        self.events.pop(identifier, None)


class DeliveryTests(unittest.TestCase):
    def test_legacy_increase_push_is_retired_before_any_delivery(self):
        state = legacy_increase_plan()
        api = FakeApi()
        persisted = []
        self.assertEqual(deliver(state, api, NOW, persist=lambda: persisted.append(True)), [])
        self.assertTrue(persisted)
        self.assertEqual(state["outbox"], {})
        kinds = {e["extendedProperties"]["private"]["kind"] for e in api.events.values()}
        self.assertEqual(kinds, {"flight", "alert"})
        self.assertTrue(all(e["reminders"]["overrides"] == [] for e in api.events.values()))

    def alert(self, identifier, route="PA", day=DAY, when=NOW, legacy=False):
        body = copy.deepcopy(next(j["body"] for j in plan(seeded(), 218)["outbox"].values()
                                  if j["kind"] == "alert"))
        body["summary"] = f"🔔{route} 9,00%"
        body["description"] = observation_text(route, day, when.isoformat())
        body["start"] = {"date": day}
        body["end"] = {"date": (date.fromisoformat(day) + timedelta(days=1)).isoformat()}
        private = body["extendedProperties"]["private"]
        private.update(route=route, date=day, observed_at=when.isoformat())
        if legacy:
            private.pop("observed_at")
        return {**body, "id": identifier, "status": "confirmed", "created": when.isoformat()}

    def push(self, identifier, when, color="10"):
        body = copy.deepcopy(next(j["body"] for j in plan(seeded(), 160)["outbox"].values()
                                  if j["kind"] == "push"))
        body.update(id=identifier, status="confirmed", colorId=color,
                    start={"dateTime": when.isoformat()},
                    end={"dateTime": (when + timedelta(minutes=1)).isoformat()})
        return body

    def test_expired_drop_and_legacy_increase_pushes_removed_for_future_flight_dates(self):
        api = FakeApi()
        for color in ("10", "11"):
            api.events[color] = self.push(color, NOW - timedelta(minutes=20), color)
        api.events["alert"] = self.alert("alert")
        state = seeded()
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(set(api.events), {"alert"})
        self.assertEqual(state["diagnostic"]["expired_pushes_deleted"], 2)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(state["diagnostic"]["expired_pushes_deleted"], 0)
        self.assertEqual(api.inserts, 0)

    def test_push_cleanup_waits_ten_minutes_after_end_in_any_timezone(self):
        api = FakeApi()
        times = {"boundary": NOW - timedelta(minutes=11),
                 "too-soon": NOW - timedelta(minutes=11) + timedelta(seconds=1),
                 "just-finished": NOW - timedelta(minutes=1),
                 "future": NOW + timedelta(minutes=5)}
        for identifier, when in times.items():
            api.events[identifier] = self.push(identifier, when.astimezone(timezone.utc))
        self.assertEqual(deliver(seeded(), api, NOW), [])
        self.assertEqual(set(api.events), set(times) - {"boundary"})

    def test_expired_push_cleanup_preserves_unrelated_and_ambiguous_events(self):
        api = FakeApi()
        for identifier in ("foreign", "attendee", "recurring", "instance", "invalid-date",
                           "missing-change", "naive", "all-day", "long-duration"):
            event = self.push(identifier, NOW - timedelta(hours=1))
            if identifier == "foreign":
                event["extendedProperties"]["private"]["managed_by"] = "another-app"
            elif identifier == "attendee":
                event["attendees"] = [{"email": "someone@example.com"}]
            elif identifier == "recurring":
                event["recurrence"] = ["RRULE:FREQ=DAILY"]
            elif identifier == "instance":
                event["recurringEventId"] = "series"
            elif identifier == "invalid-date":
                event["extendedProperties"]["private"]["date"] = "invalid"
            elif identifier == "missing-change":
                event["extendedProperties"]["private"].pop("change_id")
            elif identifier == "naive":
                event["start"]["dateTime"] = "2026-11-05T10:00:00"
            elif identifier == "all-day":
                event["start"] = {"date": DAY}
            else:
                event["end"]["dateTime"] = (NOW + timedelta(days=1)).isoformat()
            api.events[identifier] = event
        original = copy.deepcopy(api.events)
        self.assertEqual(deliver(seeded(), api, NOW), [])
        self.assertEqual(api.events, original)
        self.assertEqual(api.deleted, [])

    def test_failed_expired_push_deletion_is_reported_and_retried(self):
        api = FakeApi()
        api.events["old-push"] = self.push("old-push", NOW - timedelta(hours=1))
        api.fail_delete = True
        state = seeded()
        self.assertTrue(deliver(state, api, NOW))
        self.assertIn("old-push", api.events)
        self.assertEqual(state["diagnostic"]["expired_pushes_deleted"], 0)
        api.fail_delete = False
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertNotIn("old-push", api.events)
        self.assertEqual(state["diagnostic"]["expired_pushes_deleted"], 1)

    def test_expired_push_cleanup_rechecks_timing_and_verifies_deletion(self):
        class RescheduledApi(FakeApi):
            def get(self, identifier):
                event = super().get(identifier)
                event["start"] = {"dateTime": (NOW + timedelta(minutes=5)).isoformat()}
                event["end"] = {"dateTime": (NOW + timedelta(minutes=6)).isoformat()}
                return event
        class NoDeleteApi(FakeApi):
            def delete(self, identifier):
                pass
        for api in (RescheduledApi(), NoDeleteApi()):
            api.events["old-push"] = self.push("old-push", NOW - timedelta(hours=1))
            self.assertTrue(deliver(seeded(), api, NOW))
            self.assertIn("old-push", api.events)

    def test_expired_push_retirement_survives_replay_of_unacknowledged_plan(self):
        class TombstoneApi(FakeApi):
            def delete(self, identifier):
                self.deleted.append(identifier)
                self.events[identifier]["status"] = "cancelled"
        original = plan(seeded(), 160)
        api = TombstoneApi()
        self.assertEqual(deliver(copy.deepcopy(original), api, NOW), [])
        push_id = next(e["id"] for e in api.events.values()
                       if e["extendedProperties"]["private"]["kind"] == "push")
        restarted = copy.deepcopy(original)
        persisted = []
        later = NOW + timedelta(hours=2)
        self.assertEqual(deliver(restarted, api, later,
                                 persist=lambda: persisted.append(copy.deepcopy(restarted))), [])
        self.assertTrue(persisted)
        self.assertFalse(any(j["kind"] == "push" for j in persisted[0]["outbox"].values()))
        self.assertEqual(api.events[push_id]["status"], "cancelled")
        self.assertEqual(deliver(original, api, later), [])
        self.assertEqual(api.inserts, 3)
        self.assertFalse(original["outbox"])

    def test_existing_alerts_reduced_to_latest_for_each_route_and_date(self):
        api = FakeApi()
        for route in ("PA", "AP"):
            for number in range(3):
                identifier = f"{route}-{number}"
                api.events[identifier] = self.alert(identifier, route, "2026-11-17",
                                                     NOW + timedelta(hours=number), legacy=True)
        self.assertEqual(deliver(seeded(), api, NOW), [])
        self.assertEqual(set(api.events), {"PA-2", "AP-2"})
        self.assertEqual(api.inserts, 0)

    def test_cleanup_covers_past_and_distant_future_and_keeps_foreign_entries(self):
        class BoundsApi(FakeApi):
            def list(self, start, end):
                self.bounds = start, end
                return super().list(start, end)
        api = BoundsApi()
        for day in ("2020-01-01", "2035-01-01"):
            for number in (0, 1):
                identifier = f"{day}-{number}"
                api.events[identifier] = self.alert(identifier, day=day,
                                                     when=NOW + timedelta(hours=number))
        foreign = self.alert("personal")
        foreign["extendedProperties"]["private"]["managed_by"] = "another-app"
        api.events["personal"] = copy.deepcopy(foreign)
        self.assertEqual(deliver(seeded(), api, NOW), [])
        self.assertEqual(api.bounds, (None, None))
        self.assertEqual(set(api.events), {"2035-01-01-1", "personal"})
        self.assertEqual(api.events["personal"], foreign)

    def test_past_flights_alerts_and_pushes_removed_but_today_and_future_kept(self):
        api = FakeApi()
        yesterday = (NOW.date() - timedelta(days=1)).isoformat()
        for route in ("PA", "AP"):
            for kind in ("flight", "alert", "push"):
                identifier = f"past-{route}-{kind}"
                event = self.alert(identifier, route=route, day=yesterday)
                event["extendedProperties"]["private"]["kind"] = kind
                if kind == "push":
                    when = NOW - timedelta(days=1)
                    event["start"] = {"dateTime": when.isoformat()}
                    event["end"] = {"dateTime": (when + timedelta(minutes=1)).isoformat()}
                if kind == "flight":
                    event["extendedProperties"]["private"].pop("date")  # Legacy price entry.
                api.events[identifier] = event
        for day in (NOW.date().isoformat(), DAY):
            api.events[day] = self.alert(day, day=day)
        state = seeded()
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(set(api.events), {NOW.date().isoformat(), DAY})
        self.assertEqual(state["diagnostic"]["past_events_deleted"], 6)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(state["diagnostic"]["past_events_deleted"], 0)
        self.assertEqual(len(api.deleted), 6)

    def test_past_cleanup_preserves_foreign_attendee_recurring_and_ambiguous_events(self):
        api = FakeApi()
        past = (NOW.date() - timedelta(days=1)).isoformat()
        for identifier in ("foreign", "attendee", "recurring", "mismatch", "invalid", "future-push"):
            event = self.alert(identifier, day=past)
            private = event["extendedProperties"]["private"]
            if identifier == "foreign":
                event.pop("extendedProperties")
            elif identifier == "attendee":
                event["attendees"] = [{"email": "someone@example.com"}]
            elif identifier == "recurring":
                event["recurrence"] = ["RRULE:FREQ=DAILY"]
            elif identifier == "mismatch":
                private["date"] = "2020-01-01"
            elif identifier == "invalid":
                private["date"] = "invalid"
            else:
                private["kind"] = "push"
                event["start"] = {"dateTime": NOW.isoformat()}
                event["end"] = {"dateTime": (NOW + timedelta(minutes=1)).isoformat()}
            api.events[identifier] = event
        original = copy.deepcopy(api.events)
        self.assertEqual(deliver(seeded(), api, NOW), [])
        self.assertEqual(api.events, original)
        self.assertEqual(api.deleted, [])

    def test_failed_past_deletion_is_reported_and_retried(self):
        api = FakeApi()
        api.events["past"] = self.alert("past", day="2020-01-01")
        api.fail_delete = True
        self.assertTrue(deliver(seeded(), api, NOW))
        self.assertIn("past", api.events)
        api.fail_delete = False
        self.assertEqual(deliver(seeded(), api, NOW), [])
        self.assertNotIn("past", api.events)

    def test_past_cleanup_rechecks_identity_and_verifies_deletion(self):
        class ChangedApi(FakeApi):
            def get(self, identifier):
                event = super().get(identifier)
                event["attendees"] = [{"email": "someone@example.com"}]
                return event
        class NoDeleteApi(FakeApi):
            def delete(self, identifier):
                pass
        for api in (ChangedApi(), NoDeleteApi()):
            api.events["past"] = self.alert("past", day="2020-01-01")
            self.assertTrue(deliver(seeded(), api, NOW))
            self.assertIn("past", api.events)

    def test_delivery_after_midnight_drops_old_queue_without_recreating_events(self):
        state = plan(seeded(), 160)
        api = FakeApi()
        next_day = NOW.replace(month=12, day=18, hour=0, minute=1)
        persisted = []
        self.assertEqual(deliver(state, api, next_day, persist=lambda: persisted.append(True)), [])
        self.assertEqual(state["outbox"], {})
        self.assertEqual(state["prices"], {})
        self.assertTrue(persisted)
        self.assertEqual(api.inserts, 0)

    def test_past_cleanup_uses_warsaw_date_even_for_utc_clock(self):
        api = FakeApi()
        api.events["yesterday"] = self.alert("yesterday", day="2026-11-04")
        api.events["today"] = self.alert("today", day="2026-11-05")
        clock = NOW.replace(hour=0, minute=30).astimezone(timezone.utc)
        self.assertEqual(clock.date().isoformat(), "2026-11-04")
        self.assertEqual(deliver(seeded(), api, clock), [])
        self.assertEqual(set(api.events), {"today"})

    def test_old_alert_preserved_until_new_alert_readback_succeeds(self):
        state = plan(seeded(), 218)
        api = FakeApi()
        api.events["previous"] = self.alert("previous", when=NOW - timedelta(hours=1))
        api.fail_insert_after_write = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertIn("previous", api.events)
        self.assertFalse(api.deleted)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertNotIn("previous", api.events)
        self.assertEqual(len([e for e in api.events.values()
                              if e["extendedProperties"]["private"]["kind"] == "alert"]), 1)

    def test_delete_failure_keeps_replacement_pending_and_push_waits(self):
        state = plan(seeded(), 160)
        api = FakeApi()
        api.events["previous"] = self.alert("previous", when=NOW - timedelta(hours=1))
        api.fail_delete = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertTrue(any(j["kind"] == "alert" for j in state["outbox"].values()))
        self.assertFalse(any(e["extendedProperties"]["private"]["kind"] == "push"
                             for e in api.events.values()))
        api.fail_delete = False
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertFalse(state["outbox"])
        self.assertNotIn("previous", api.events)
        self.assertEqual(len([e for e in api.events.values()
                              if e["extendedProperties"]["private"]["kind"] == "alert"]), 1)
        self.assertEqual(len([e for e in api.events.values()
                              if e["extendedProperties"]["private"]["kind"] == "push"]), 1)

    def test_unverified_winner_prevents_historical_cleanup(self):
        api = FakeApi()
        api.events["old"] = self.alert("old", when=NOW - timedelta(hours=1))
        api.events["new"] = self.alert("new")
        api.fail_readback = True
        self.assertTrue(deliver(seeded(), api, NOW))
        self.assertFalse(api.deleted)
        self.assertEqual(set(api.events), {"old", "new"})

    def test_newer_existing_alert_wins_over_a_late_old_queue_job(self):
        api = FakeApi()
        api.events["newer"] = self.alert("newer", when=NOW + timedelta(hours=1))
        state = plan(seeded(), 160)
        self.assertEqual(deliver(state, api, NOW), [])
        alerts = [e for e in api.events.values()
                  if e["extendedProperties"]["private"]["kind"] == "alert"]
        self.assertEqual([e["id"] for e in alerts], ["newer"])
        self.assertFalse(any(e["extendedProperties"]["private"]["kind"] == "push"
                             for e in api.events.values()))
        self.assertFalse(state["outbox"])

    def test_migration_of_multiple_pending_alerts_does_not_send_old_push(self):
        old = legacy_increase_plan()
        when = NOW + timedelta(hours=1)
        new = prepare(seeded(), {KEY: {"price": "160", "observed_at": when.isoformat()}},
                      {"expected": 1, "read": 1, "errors": [], "checked_at": when.isoformat()}, when)
        new["outbox"].update({k: v for k, v in old["outbox"].items() if v["kind"] != "flight"})
        api = FakeApi()
        self.assertEqual(deliver(new, api, when), [])
        alerts = [e for e in api.events.values()
                  if e["extendedProperties"]["private"]["kind"] == "alert"]
        pushes = [e for e in api.events.values()
                  if e["extendedProperties"]["private"]["kind"] == "push"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(pushes), 1)
        self.assertEqual(alerts[0]["colorId"], "10")
        self.assertEqual(pushes[0]["colorId"], "10")

    def test_pending_old_push_without_alert_job_is_retired_if_newer_alert_exists(self):
        state = plan(seeded(), 160)
        state["outbox"] = {k: j for k, j in state["outbox"].items() if j["kind"] == "push"}
        api = FakeApi()
        api.events["newer"] = self.alert("newer", when=NOW + timedelta(hours=1))
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 0)
        self.assertFalse(state["outbox"])

    def test_weekly_cleanup_preserves_owned_event_with_attendees(self):
        api = FakeApi()
        meeting = self.alert("meeting")
        meeting["attendees"] = [{"email": "guest@example.com"}]
        api.events["meeting"] = copy.deepcopy(meeting)
        state = plan(empty_state(), 250)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.events["meeting"], meeting)

    def test_attendee_event_and_mismatched_date_are_not_cleanup_candidates(self):
        api = FakeApi()
        for identifier in ("old", "new", "meeting", "moved"):
            api.events[identifier] = self.alert(identifier, when=NOW - timedelta(hours=1)
                                                 if identifier == "old" else NOW)
        api.events["meeting"]["attendees"] = [{"email": "guest@example.com"}]
        api.events["moved"]["start"]["date"] = "2026-12-18"
        self.assertEqual(deliver(seeded(), api, NOW), [])
        self.assertEqual(set(api.events), {"new", "meeting", "moved"})

    def test_deleted_price_recreated_from_fresh_quote_and_id_remembered(self):
        state = plan(seeded(), 200)
        original = copy.deepcopy(state)
        api = FakeApi()
        old_id = state["outbox"]["price:" + KEY]["event_id"]
        api.events[old_id] = {"id": old_id, "status": "cancelled"}
        self.assertEqual(deliver(state, api, NOW), [])
        replacement = state["prices"][KEY]["event_id"]
        self.assertNotEqual(replacement, old_id)
        self.assertEqual(api.events[old_id]["status"], "cancelled")
        self.assertEqual(api.events[replacement]["summary"], "PA 200")
        self.assertEqual(api.inserts, 1)
        # Simulate losing the Git acknowledgement after remote success.
        self.assertEqual(deliver(original, api, NOW), [])
        self.assertEqual(api.inserts, 1)
        again = plan(state, 200)
        self.assertEqual(again["outbox"]["price:" + KEY]["event_id"], replacement)

    def test_repeated_price_deletion_has_one_active_replacement(self):
        state = plan(seeded(), 200)
        api = FakeApi()
        deliver(state, api, NOW)
        for _ in range(2):
            active_id = state["prices"][KEY]["event_id"]
            api.events[active_id]["status"] = "cancelled"
            state = plan(state, 200)
            self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(len([e for e in api.events.values() if e["status"] == "confirmed"]), 1)
        self.assertFalse(state["outbox"])

    def test_deleted_price_without_fresh_quote_is_not_restored(self):
        state = plan(seeded(), 200)
        api = FakeApi()
        old_id = state["outbox"]["price:" + KEY]["event_id"]
        api.events[old_id] = {"id": old_id, "status": "cancelled"}
        state = prepare(state, {}, {"expected": 1, "read": 0, "errors": [],
                                   "complete": False, "checked_at": NOW.isoformat()}, NOW)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 0)
        self.assertEqual(health_errors(state), [])

    def test_old_direct_delivery_cannot_restore_price_without_new_read(self):
        state = plan(seeded(), 200)
        api = FakeApi()
        old_id = state["outbox"]["price:" + KEY]["event_id"]
        api.events[old_id] = {"id": old_id, "status": "cancelled"}
        self.assertTrue(deliver(state, api, NOW + timedelta(hours=2)))
        self.assertEqual(api.inserts, 0)
        self.assertTrue(state["outbox"])

    def test_lost_replacement_insert_response_recovers_without_duplicate(self):
        state = plan(seeded(), 200)
        before = copy.deepcopy(state)
        api = FakeApi()
        old_id = state["outbox"]["price:" + KEY]["event_id"]
        api.events[old_id] = {"id": old_id, "status": "cancelled"}
        api.fail_insert_after_write = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertEqual(deliver(before, api, NOW), [])
        self.assertEqual(api.inserts, 1)
        self.assertFalse(before["outbox"])

    def test_unverifiable_conflict_does_not_assume_deletion_or_duplicate(self):
        state = plan(seeded(), 200)
        old_id = state["outbox"]["price:" + KEY]["event_id"]
        class HiddenTombstoneApi(FakeApi):
            def get(self, identifier):
                return None if identifier == old_id else super().get(identifier)
        api = HiddenTombstoneApi()
        api.events[old_id] = {"id": old_id, "status": "cancelled"}
        self.assertTrue(deliver(state, api, NOW))
        self.assertEqual(api.inserts, 0)
        self.assertEqual(state["outbox"]["price:" + KEY]["event_id"], old_id)

    def test_deleted_alert_and_its_pending_push_not_recreated(self):
        state = plan(seeded(), 160)
        api = FakeApi()
        alert = next(j for j in state["outbox"].values() if j["kind"] == "alert")
        api.events[alert["event_id"]] = {"id": alert["event_id"], "status": "cancelled"}
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 1)  # Price only, no replacement alert or push.
        self.assertFalse(state["outbox"])
        self.assertTrue(state["diagnostic"]["warnings"])

    def test_deleted_push_not_recreated(self):
        state = plan(seeded(), 160)
        api = FakeApi()
        push = next(j for j in state["outbox"].values() if j["kind"] == "push")
        api.events[push["event_id"]] = {"id": push["event_id"], "status": "cancelled"}
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 2)  # Price and alert only.
        self.assertFalse(state["outbox"])

    def test_read_error_during_weekly_retry_does_not_clear_old_alerts(self):
        api = FakeApi()
        deliver(plan(seeded(), 160), api, NOW)
        state = plan(empty_state(), 250)
        state = prepare(state, {}, {"expected": 1, "read": 0, "complete": False,
                                   "errors": ["API unavailable"], "checked_at": NOW.isoformat()}, NOW)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.deleted, [])
        self.assertIsNotNone(state["weekly_pending"])
        self.assertIn("API unavailable", health_errors(state))

    def test_delivers_price_alert_and_push_with_exact_reminders(self):
        state = plan(seeded(), 160)
        api = FakeApi()
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(state["outbox"], {})
        kinds = {e["extendedProperties"]["private"]["kind"]: e for e in api.events.values()}
        self.assertEqual(set(kinds), {"flight", "alert", "push"})
        self.assertEqual(kinds["push"]["start"]["dateTime"], (NOW + timedelta(minutes=5)).isoformat())
        self.assertEqual(kinds["push"]["reminders"]["overrides"], [{"method": "popup", "minutes": 0}])
        self.assertEqual(kinds["alert"]["start"], {"date": DAY})
        self.assertEqual(kinds["alert"]["reminders"]["overrides"], [])

    def test_price_updates_same_event_and_read_time_even_without_price_change(self):
        state = plan(seeded(), 218)
        api = FakeApi()
        deliver(state, api, NOW)
        before = api.inserts
        new = plan(state, 218)
        new["outbox"]["price:" + KEY]["body"]["description"] += "\nNew observed time"
        deliver(new, api, NOW)
        self.assertEqual(api.inserts, before)
        self.assertEqual(api.patches, 1)

    def test_lost_insert_response_recovers_without_duplicate(self):
        state = plan(seeded(), 160)
        api = FakeApi()
        api.fail_insert_after_write = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertTrue(state["outbox"])
        self.assertEqual(deliver(state, api, NOW + timedelta(hours=1)), [])
        self.assertEqual(api.inserts, 3)
        self.assertFalse(state["outbox"])

    def test_failed_readback_remains_pending_until_success(self):
        state = plan(seeded(), 160)
        api = FakeApi()
        api.fail_readback = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertEqual(len(state["outbox"]), 3)
        api.fail_readback = False
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 3)

    def test_restart_after_remote_success_preserves_push_time(self):
        original = plan(seeded(), 160)
        api = FakeApi()
        completed = copy.deepcopy(original)
        deliver(completed, api, NOW)
        push_before = next(e for e in api.events.values()
                           if e["extendedProperties"]["private"]["kind"] == "push")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            save_state(original, path)
            restored = load_state(path)
            self.assertEqual(deliver(restored, api, NOW + timedelta(minutes=2)), [])
        self.assertEqual(api.inserts, 3)
        push_after = api.events[push_before["id"]]
        self.assertEqual(push_before["start"], push_after["start"])

    def test_weekly_reset_only_deletes_owned_alerts_after_prices_verified(self):
        api = FakeApi()
        hourly = plan(seeded(), 160)
        deliver(hourly, api, NOW)
        personal = {"id": "personal", "summary": "PA holiday",
                    "description": "Monitoring oferty", "status": "confirmed",
                    "start": {"date": DAY}}
        api.events["personal"] = personal
        state = plan(empty_state(), 250)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertIsNone(state["weekly_pending"])
        self.assertEqual(state["last_week"], "2026-11-02")
        self.assertEqual(len(api.deleted), 2)
        self.assertEqual(api.events["personal"], personal)
        self.assertEqual(len(api.events), 2)

    def test_weekly_failed_price_readback_does_not_delete_alerts(self):
        api = FakeApi()
        deliver(plan(seeded(), 160), api, NOW)
        state = plan(empty_state(), 250)
        api.fail_readback = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertIsNotNone(state["weekly_pending"])
        self.assertEqual(api.deleted, [])

    def test_weekly_cleanup_failure_retries_next_run(self):
        api = FakeApi()
        deliver(plan(seeded(), 160), api, NOW)
        state = plan(empty_state(), 250)
        api.fail_delete = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertIsNotNone(state["weekly_pending"])
        api.fail_delete = False
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertIsNone(state["weekly_pending"])

    def test_existing_owned_legacy_flight_adopted_without_duplicate(self):
        api = FakeApi()
        api.events["old"] = {"id": "old", "status": "confirmed", "summary": "PA199",
                             "start": {"date": DAY},
                             "extendedProperties": {"private": {
                                 "managed_by": MARKER, "kind": "flight", "route": "PA"}}}
        state = plan(seeded(), 200)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 0)
        self.assertEqual(api.events["old"]["summary"], "PA 200")
        # Legacy Google IDs need not be our 64-character hashes.
        again = plan(state, 200)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            save_state(again, path)
            restored = load_state(path)
        self.assertEqual(deliver(restored, api, NOW), [])
        self.assertEqual(api.inserts, 0)

    def test_duplicate_owned_flights_block_update_and_cleanup(self):
        api = FakeApi()
        for identifier in ("old1", "old2"):
            api.events[identifier] = {"id": identifier, "status": "confirmed",
                                     "start": {"date": DAY},
                                     "extendedProperties": {"private": {
                                         "managed_by": MARKER, "kind": "flight", "route": "PA"}}}
        state = plan(empty_state(), 200)
        self.assertTrue(deliver(state, api, NOW))
        self.assertEqual(api.inserts, 0)
        self.assertEqual(api.patches, 0)
        self.assertEqual(api.deleted, [])

    def test_foreign_event_id_never_overwritten(self):
        api = FakeApi()
        state = plan(seeded(), 200)
        job = next(iter(state["outbox"].values()))
        api.events[job["event_id"]] = {"id": job["event_id"], "summary": "private"}
        self.assertTrue(deliver(state, api, NOW))
        self.assertEqual(api.patches, 0)
        self.assertEqual(api.events[job["event_id"]]["summary"], "private")

    def test_wrong_readback_title_keeps_alert_pending(self):
        class WrongApi(FakeApi):
            def get(self, identifier):
                event = super().get(identifier)
                if event and event["extendedProperties"]["private"]["kind"] == "alert":
                    event["summary"] = "wrong"
                return event
        state = plan(seeded(), 218)
        self.assertTrue(deliver(state, WrongApi(), NOW))
        self.assertTrue(any(j["kind"] == "alert" for j in state["outbox"].values()))


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class TransportTests(unittest.TestCase):
    def test_unbounded_owned_event_listing_omits_time_filters(self):
        calls = []
        def opener(req, timeout):
            calls.append(req.full_url)
            return Response(b'{"items":[]}')
        api = GoogleCalendarApi("client", "secret", "refresh", opener)
        api.token, api.expires = "test-token", float("inf")
        self.assertEqual(api.list(None, None), [])
        self.assertNotIn("timeMin", calls[0])
        self.assertNotIn("timeMax", calls[0])
        self.assertIn("privateExtendedProperty=managed_by%3D", calls[0])

    def test_gone_event_is_distinguished_from_never_existing_id(self):
        def opener(req, timeout):
            code = 410 if req.full_url.endswith("/deleted") else 404
            raise HTTPError(req.full_url, code, "gone", {}, None)
        api = GoogleCalendarApi("client", "secret", "refresh", opener)
        api.token = "test-token"
        api.expires = float("inf")
        self.assertEqual(api.get("deleted"), {"id": "deleted", "status": "cancelled"})
        self.assertIsNone(api.get("never-existed"))

    def test_only_one_calendar_auth_and_pagination(self):
        calls = []
        def opener(req, timeout):
            calls.append(req)
            if "oauth2" in req.full_url:
                return Response(b'{"access_token":"secret-access","expires_in":3600}')
            if "pageToken=next" not in req.full_url:
                return Response(b'{"items":[{"id":"one"}],"nextPageToken":"next"}')
            return Response(b'{"items":[{"id":"two"}]}')
        api = GoogleCalendarApi("client", "secret", "refresh", opener)
        events = api.list(NOW.isoformat(), (NOW + timedelta(days=365)).isoformat())
        self.assertEqual([e["id"] for e in events], ["one", "two"])
        self.assertTrue(all("ryanairsamolot%40gmail.com" in r.full_url for r in calls[1:]))
        self.assertTrue(all("privateExtendedProperty=" in r.full_url for r in calls[1:]))
        self.assertEqual(calls[1].get_header("Authorization"), "Bearer secret-access")

    def test_401_refreshes_token(self):
        tokens = 0
        accesses = 0
        def opener(req, timeout):
            nonlocal tokens, accesses
            if "oauth2" in req.full_url:
                tokens += 1
                return Response(json.dumps({"access_token": str(tokens)}).encode())
            accesses += 1
            if accesses == 1:
                raise HTTPError(req.full_url, 401, "Unauthorized", {}, None)
            return Response(b'{"id":"ok"}')
        api = GoogleCalendarApi("client", "secret", "refresh", opener)
        self.assertEqual(api.get("ok")["id"], "ok")
        self.assertEqual(tokens, 2)

    def test_missing_secrets_fail_before_network(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(CalendarError):
                GoogleCalendarApi.from_environment()


if __name__ == "__main__":
    unittest.main()
