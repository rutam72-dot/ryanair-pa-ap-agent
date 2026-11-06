import copy
import io
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from calendar_sync import CalendarError, GoogleCalendarApi, deliver
from monitor import CALENDAR, MARKER, load_state, save_state, prepare, health_errors
from test_simple_monitor import DAY, KEY, NOW, plan, seeded, empty_state


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
        self.assertEqual(api.events[replacement]["summary"], "PA 200.00")
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

    def test_reserved_deleted_id_with_404_readback_uses_stable_successor(self):
        state = plan(seeded(), 200)
        old_id = state["outbox"]["price:" + KEY]["event_id"]
        class HiddenTombstoneApi(FakeApi):
            def get(self, identifier):
                return None if identifier == old_id else super().get(identifier)
        api = HiddenTombstoneApi()
        api.events[old_id] = {"id": old_id, "status": "cancelled"}
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 1)
        self.assertNotEqual(state["prices"][KEY]["event_id"], old_id)

    def test_deleted_alert_and_its_pending_push_not_recreated(self):
        state = plan(seeded(), 240)
        api = FakeApi()
        alert = next(j for j in state["outbox"].values() if j["kind"] == "alert")
        api.events[alert["event_id"]] = {"id": alert["event_id"], "status": "cancelled"}
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 1)  # Price only, no replacement alert or push.
        self.assertFalse(state["outbox"])
        self.assertTrue(state["diagnostic"]["warnings"])

    def test_deleted_push_not_recreated(self):
        state = plan(seeded(), 240)
        api = FakeApi()
        push = next(j for j in state["outbox"].values() if j["kind"] == "push")
        api.events[push["event_id"]] = {"id": push["event_id"], "status": "cancelled"}
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 2)  # Price and alert only.
        self.assertFalse(state["outbox"])

    def test_read_error_during_weekly_retry_does_not_clear_old_alerts(self):
        api = FakeApi()
        deliver(plan(seeded(), 240), api, NOW)
        state = plan(empty_state(), 250)
        state = prepare(state, {}, {"expected": 1, "read": 0, "complete": False,
                                   "errors": ["API unavailable"], "checked_at": NOW.isoformat()}, NOW)
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.deleted, [])
        self.assertIsNotNone(state["weekly_pending"])
        self.assertIn("API unavailable", health_errors(state))

    def test_delivers_price_alert_and_push_with_exact_reminders(self):
        state = plan(seeded(), 240)
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
        state = plan(seeded(), 240)
        api = FakeApi()
        api.fail_insert_after_write = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertTrue(state["outbox"])
        self.assertEqual(deliver(state, api, NOW + timedelta(hours=1)), [])
        self.assertEqual(api.inserts, 3)
        self.assertFalse(state["outbox"])

    def test_failed_readback_remains_pending_until_success(self):
        state = plan(seeded(), 240)
        api = FakeApi()
        api.fail_readback = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertEqual(len(state["outbox"]), 3)
        api.fail_readback = False
        self.assertEqual(deliver(state, api, NOW), [])
        self.assertEqual(api.inserts, 3)

    def test_restart_after_remote_success_preserves_push_time(self):
        original = plan(seeded(), 240)
        api = FakeApi()
        completed = copy.deepcopy(original)
        deliver(completed, api, NOW)
        push_before = next(e for e in api.events.values()
                           if e["extendedProperties"]["private"]["kind"] == "push")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            save_state(original, path)
            restored = load_state(path)
            self.assertEqual(deliver(restored, api, NOW + timedelta(hours=2)), [])
        self.assertEqual(api.inserts, 3)
        push_after = api.events[push_before["id"]]
        self.assertEqual(push_before["start"], push_after["start"])

    def test_weekly_reset_only_deletes_owned_alerts_after_prices_verified(self):
        api = FakeApi()
        hourly = plan(seeded(), 240)
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
        deliver(plan(seeded(), 240), api, NOW)
        state = plan(empty_state(), 250)
        api.fail_readback = True
        self.assertTrue(deliver(state, api, NOW))
        self.assertIsNotNone(state["weekly_pending"])
        self.assertEqual(api.deleted, [])

    def test_weekly_cleanup_failure_retries_next_run(self):
        api = FakeApi()
        deliver(plan(seeded(), 240), api, NOW)
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
        self.assertEqual(api.events["old"]["summary"], "PA 200.00")
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
