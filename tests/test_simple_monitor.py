import copy
import json
from html.parser import HTMLParser
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from agent import Fare
from monitor import (TZ, booking_url, collect, empty_state, event_id, load_state,
                     prepare, price_color, price_payload, save_state, week_due, main, health_errors)
from ryanair_client import RyanairReadError
from ryanair_schedule import ScheduleReadError

NOW = datetime(2026, 11, 5, 12, 7, tzinfo=TZ)
DAY = "2026-12-17"
KEY = "PA:" + DAY


def quotes(amount):
    return {KEY: {"price": str(amount), "observed_at": NOW.isoformat()}}


def seeded(amount="200"):
    state = empty_state()
    state["last_week"] = week_due(state, NOW)
    state["prices"][KEY] = {**quotes(amount)[KEY], "reference": str(amount)}
    return state


def plan(state, amount, complete=True):
    return prepare(state, quotes(amount), {
        "complete": complete, "expected": 1 if complete else 2, "read": 1,
        "checked_at": NOW.isoformat(), "warnings": [],
        "errors": [] if complete else ["Simulated technical read failure"],
    }, NOW)


def legacy_increase_plan():
    """Persisted increase push created by the policy before drops-only delivery."""
    state = plan(seeded(), 240)
    key, alert = next((k, j) for k, j in state["outbox"].items() if j["kind"] == "alert")
    push = copy.deepcopy(alert)
    push["kind"] = "push"
    push["event_id"] = event_id(key + "-push")
    push["body"]["extendedProperties"]["private"]["kind"] = "push"
    push["body"].pop("start")
    push["body"].pop("end")
    push["body"]["reminders"] = {"useDefault": False,
                                 "overrides": [{"method": "popup", "minutes": 0}]}
    state["outbox"][key + "-push"] = push
    return state


class DomainTests(unittest.TestCase):
    def test_new_qualifying_change_supersedes_pending_alert_and_push_for_same_slot(self):
        old = plan(seeded(), 160)
        old_ids = {j["event_id"] for j in old["outbox"].values() if j["kind"] != "flight"}
        new = plan(old, 180)
        alerts = [j for j in new["outbox"].values() if j["kind"] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertFalse(any(j["event_id"] in old_ids for j in new["outbox"].values()))
        self.assertFalse(any(j["kind"] == "push" for j in new["outbox"].values()))
        self.assertEqual(alerts[0]["body"]["extendedProperties"]["private"]["observed_at"],
                         NOW.isoformat())

    def test_price_color_boundaries_and_whole_number_title(self):
        for amount, color in [(0, "10"), ("299.99", "10"), (300, "5"),
                              ("499.99", "5"), (500, "6"), ("649.99", "6"),
                              (650, "11")]:
            self.assertEqual(price_color(amount), color)
        body = price_payload("PA", DAY, quotes("218.5")[KEY])
        self.assertEqual(body["summary"], "PA 219")
        for amount, displayed in [("218.49", "218"), ("218.50", "219"),
                                  ("218.99", "219"), ("0.00", "0")]:
            for route in ("PA", "AP"):
                with self.subTest(amount=amount, route=route):
                    self.assertEqual(price_payload(route, DAY, quotes(amount)[KEY])["summary"],
                                     f"{route} {displayed}")
        self.assertEqual(body["start"], {"date": DAY})
        self.assertIn("05.11.2026, 12:07", body["description"])
        self.assertTrue(body["description"].startswith("<p>(POZ)-(ALC)</p>"))
        self.assertTrue(body["description"].endswith("<p>Cena taryfy Basic niepotwierdzona.</p>"))
        self.assertNotIn("Poznań", body["description"])
        self.assertEqual(body["reminders"], {"useDefault": False, "overrides": []})

    def test_rendered_booking_link_preserves_query_and_paragraphs(self):
        class DescriptionParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.href = None
                self.paragraphs = 0
            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.href = dict(attrs)["href"]
                if tag == "p":
                    self.paragraphs += 1
        body = price_payload("AP", DAY, quotes("218.5")[KEY])
        parser = DescriptionParser()
        parser.feed(body["description"])
        self.assertEqual(parser.href, booking_url("AP", DAY))
        self.assertEqual(parser.paragraphs, 6)
        params = parse_qs(urlparse(parser.href).query)
        self.assertEqual(params["dateOut"], [DAY])
        self.assertEqual(params["originIata"], ["ALC"])
        self.assertNotIn("dateOut;", params)

    def test_booking_links_both_directions_exact_date_one_adult(self):
        for route, origin, destination in [("PA", "POZ", "ALC"), ("AP", "ALC", "POZ")]:
            params = parse_qs(urlparse(booking_url(route, DAY)).query)
            self.assertEqual(params["originIata"], [origin])
            self.assertEqual(params["destinationIata"], [destination])
            self.assertEqual(params["dateOut"], [DAY])
            self.assertEqual(params["tpStartDate"], [DAY])
            self.assertEqual(params["adults"], ["1"])
            self.assertEqual(params["isReturn"], ["false"])

    def test_exact_eight_percent_no_alert_both_directions(self):
        for amount in (216, 184):
            state = plan(seeded(), amount)
            self.assertEqual([j["kind"] for j in state["outbox"].values()], ["flight"])

    def test_alert_above_eight_both_directions(self):
        for amount, color, percent, displayed in [
                ("218.34", "11", "9", "218"), ("181.66", "10", "9", "182"),
                ("219", "11", "10", "219"), ("181", "10", "10", "181"),
                ("216.01", "11", "8", "216"), ("183.99", "10", "8", "184")]:
            state = plan(seeded(), amount)
            alerts = [j for j in state["outbox"].values() if j["kind"] == "alert"]
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["body"]["summary"], f"🔔PA {percent}%")
            self.assertTrue(alerts[0]["body"]["description"].startswith(
                f"<p>Zmiana: 200 → {displayed} PLN</p>"))
            self.assertEqual(alerts[0]["body"]["colorId"], color)
            self.assertFalse(any(j["kind"] == "push" for j in state["outbox"].values()))
            self.assertEqual(state["prices"][KEY]["price"], amount)
            self.assertEqual(state["prices"][KEY]["reference"], amount)

    def test_push_only_for_drop_at_least_twenty_percent_in_both_routes(self):
        for route in ("PA", "AP"):
            key = route + ":" + DAY
            for amount, push_expected in ((160, True), (159, True), ("160.01", False),
                                          (240, False), (300, False)):
                with self.subTest(route=route, amount=amount):
                    initial = seeded()
                    initial["prices"][key] = initial["prices"].pop(KEY)
                    state = prepare(initial, {key: quotes(amount)[KEY]},
                                    {"expected": 1, "read": 1, "errors": []}, NOW)
                    kinds = sorted(j["kind"] for j in state["outbox"].values())
                    self.assertEqual(kinds, ["alert", "flight", "push"] if push_expected
                                     else ["alert", "flight"])
                    self.assertEqual(state["prices"][key]["reference"], str(amount))

    def test_prepare_retires_legacy_increase_push_even_without_fresh_quote(self):
        state = legacy_increase_plan()
        state = prepare(state, {}, {"expected": 1, "read": 0, "errors": []}, NOW)
        self.assertEqual([j["kind"] for j in state["outbox"].values()], ["alert"])

    def test_push_below_threshold_uses_exact_unrounded_drop(self):
        for amount in ("160.01", "160.49"):
            state = plan(seeded(), amount)
            self.assertFalse(any(j["kind"] == "push" for j in state["outbox"].values()))

    def test_cumulative_changes_compare_to_last_alert(self):
        state = plan(seeded(), 210)
        self.assertEqual(state["prices"][KEY]["reference"], "200")
        state = plan(state, 218)
        self.assertEqual(state["prices"][KEY]["reference"], "218")
        state = plan(state, 220)
        self.assertEqual(len([j for j in state["outbox"].values() if j["kind"] == "alert"]), 1)

    def test_repeated_real_transition_not_deduplicated_as_crash(self):
        state = seeded()
        identifiers = []
        for amount in (180, 200, 180):
            state = plan(state, amount)
            identifiers.append(next(j["event_id"] for j in state["outbox"].values()
                                    if j["kind"] == "alert"))
        alerts = [j for j in state["outbox"].values() if j["kind"] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(set(identifiers)), 3)
        self.assertEqual(alerts[0]["event_id"], identifiers[-1])
        repeated = plan(state, 180)
        self.assertEqual(len(repeated["outbox"]), len(state["outbox"]))

    def test_first_baseline_no_alerts_or_push(self):
        state = plan(empty_state(), 240)
        self.assertIsNotNone(state["weekly_pending"])
        self.assertEqual([j["kind"] for j in state["outbox"].values()], ["flight"])

    def test_weekly_reset_sets_reference_and_retires_pending_alerts(self):
        state = plan(seeded(), 240)
        state["last_week"] = "2026-10-26"
        state = plan(state, 250)
        self.assertEqual(state["prices"][KEY]["reference"], "250")
        self.assertEqual([j["kind"] for j in state["outbox"].values()], ["flight"])
        self.assertEqual(state["weekly_pending"], "2026-11-02")

    def test_partial_weekly_read_preserves_alerts_and_reference(self):
        state = plan(seeded(), 240)
        state["last_week"] = "2026-10-26"
        state = plan(state, 245, complete=False)
        self.assertIsNone(state["weekly_pending"])
        self.assertEqual(state["prices"][KEY]["reference"], "240")
        self.assertTrue(any(j["kind"] == "alert" for j in state["outbox"].values()))

    def test_week_boundary_and_catchup(self):
        state = seeded()
        self.assertIsNone(week_due(state, datetime(2026, 11, 9, 4, 6, tzinfo=TZ)))
        self.assertEqual(week_due(state, datetime(2026, 11, 9, 4, 7, tzinfo=TZ)), "2026-11-09")
        self.assertEqual(week_due(state, datetime(2026, 11, 11, 12, 7, tzinfo=TZ)), "2026-11-09")

    def test_missing_quote_preserves_price_and_time(self):
        state = seeded()
        result = prepare(state, {}, {"complete": False}, NOW)
        self.assertEqual(result["prices"], state["prices"])
        self.assertEqual(result["outbox"], {})

    def test_past_undelivered_push_expires(self):
        state = plan(seeded(), 160)
        for job in state["outbox"].values():
            job["day"] = "2026-11-04"
        state = prepare(state, {}, {"complete": False}, NOW)
        self.assertFalse(state["outbox"])

    def test_atomic_state_restart_preserves_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            state = plan(seeded(), 240)
            save_state(state, path)
            restored = load_state(path)
            self.assertEqual(restored, state)
            self.assertEqual(plan(restored, 240)["outbox"], state["outbox"])

    def test_weekly_retry_refreshes_baseline_without_alerts(self):
        state = plan(empty_state(), 240)
        retried = plan(state, 250)
        self.assertEqual(retried["weekly_pending"], state["weekly_pending"])
        self.assertEqual(retried["prices"][KEY]["reference"], "250")
        self.assertEqual([j["kind"] for j in retried["outbox"].values()], ["flight"])

    def test_missing_fare_is_warning_but_transport_error_is_failure(self):
        state = seeded()
        state["diagnostic"] = {"expected": 2, "read": 1, "complete": False,
                               "errors": [], "warnings": ["No current fare"], "missing": 1}
        self.assertEqual(health_errors(state), [])
        state["diagnostic"]["errors"] = ["API timeout"]
        self.assertIn("API timeout", health_errors(state))

    def test_pending_delivery_and_missing_timetable_still_fail(self):
        state = plan(seeded(), 200)
        self.assertTrue(health_errors(state))
        state["outbox"] = {}
        state["diagnostic"]["expected"] = 0
        self.assertIn("No timetable dates confirmed", health_errors(state))

    def test_status_command_warns_without_writing_or_calling_google(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            state = seeded()
            state["diagnostic"] = {"expected": 2, "read": 1, "missing": 1,
                                   "complete": False, "errors": [], "warnings": ["No current fare"]}
            save_state(state, path)
            before = path.read_bytes()
            with patch("sys.argv", ["monitor.py", "status", "--state", str(path)]), \
                 patch("calendar_sync.GoogleCalendarApi.from_environment") as google, \
                 patch("builtins.print") as output:
                self.assertEqual(main(), 0)
                google.assert_not_called()
                output.assert_any_call("::warning::No current fare")
            self.assertEqual(path.read_bytes(), before)

    def test_status_command_keeps_technical_failure_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            state = seeded()
            state["diagnostic"] = {"expected": 2, "read": 1, "errors": ["API timeout"]}
            save_state(state, path)
            with patch("sys.argv", ["monitor.py", "status", "--state", str(path)]), \
                 patch("builtins.print"):
                self.assertEqual(main(), 1)

    def test_stale_pending_price_removed_without_changing_last_quote(self):
        state = plan(seeded(), 200)
        old = copy.deepcopy(state["prices"][KEY])
        result = prepare(state, {}, {"expected": 1, "read": 0, "complete": False,
                                    "errors": [], "checked_at": NOW.isoformat()}, NOW)
        self.assertEqual(result["prices"][KEY], old)
        self.assertEqual(result["outbox"], {})
        self.assertEqual(health_errors(result), [])

    def test_weekly_reset_accepts_valid_empty_offer_and_keeps_its_old_record(self):
        state = seeded()
        missing = "AP:" + DAY
        state["prices"][missing] = {**quotes("300")[KEY], "reference": "300"}
        old_missing = copy.deepcopy(state["prices"][missing])
        state["last_week"] = "2026-10-26"
        result = prepare(state, quotes("220"), {"expected": 2, "read": 1,
            "complete": False, "errors": [], "unavailable": [missing],
            "checked_at": NOW.isoformat()}, NOW)
        self.assertIsNotNone(result["weekly_pending"])
        self.assertEqual(result["prices"][KEY]["reference"], "220")
        self.assertEqual(result["prices"][missing], old_missing)
        self.assertNotIn("price:" + missing, result["outbox"])

    def test_corrupt_state_cannot_inject_unowned_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            state = plan(seeded(), 240)
            next(iter(state["outbox"].values()))["body"]["extendedProperties"]["private"]["managed_by"] = "other"
            save_state(state, path)
            with self.assertRaises(ValueError):
                load_state(path)

    def test_observation_time_is_request_completion_time(self):
        completed = NOW + timedelta(minutes=3)
        result, _ = collect(NOW,
                            lambda route, start, end: [Fare(route, start, Decimal("100"), True)],
                            lambda *args: {date(2026, 12, 17)}, clock=lambda: completed)
        self.assertTrue(all(q["observed_at"] == completed.isoformat() for q in result.values()))

    def test_preview_never_mutates_file_or_accesses_google(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            save_state(seeded(), path)
            before = path.read_bytes()
            with patch("monitor.collect", return_value=(quotes(240), {"complete": True})), \
                 patch("sys.argv", ["monitor.py", "preview", "--state", str(path)]), \
                 patch("calendar_sync.GoogleCalendarApi.from_environment") as google, \
                 patch("builtins.print"):
                self.assertEqual(main(), 0)
                google.assert_not_called()
            self.assertEqual(path.read_bytes(), before)


class CollectionTests(unittest.TestCase):
    def test_every_day_requested_even_if_it_has_old_price(self):
        calls = []
        days = {date(2026, 12, 17), date(2026, 12, 18)}
        def fetch(route, start, end):
            calls.append((route, start, end))
            return [Fare(route, start, Decimal("218.50"), True)]
        result, diagnostic = collect(NOW, fetch, lambda *args: days, clock=lambda: NOW)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(start == end for _, start, end in calls))
        self.assertEqual(len(result), 4)
        self.assertTrue(diagnostic["complete"])

    def test_partial_error_keeps_other_days_and_retries(self):
        calls = []
        def fetch(route, start, end):
            calls.append(route)
            if route == "AP":
                raise RyanairReadError("unavailable")
            return [Fare(route, start, Decimal("218.50"), True)]
        with patch("monitor.backoff.sleep"):
            result, diagnostic = collect(NOW, fetch, lambda *args: {date(2026, 12, 17)})
        self.assertEqual(len(result), 1)
        self.assertEqual(calls.count("AP"), 2)
        self.assertFalse(diagnostic["complete"])
        self.assertEqual(diagnostic["missing"], 1)

    def test_empty_prices_are_not_a_successful_weekly_reset(self):
        result, diagnostic = collect(NOW, lambda *args: [],
                                     lambda *args: {date(2026, 12, 17)})
        self.assertEqual(result, {})
        self.assertFalse(diagnostic["complete"])
        self.assertEqual(len(diagnostic["unavailable"]), 2)
        self.assertTrue(diagnostic["warnings"])
        self.assertFalse(diagnostic["errors"])

    def test_schedule_failure_does_not_erase_other_route(self):
        def schedule(route, *args):
            if route == "AP":
                raise ScheduleReadError("bad month")
            return {date(2026, 12, 17)}
        result, diagnostic = collect(
            NOW, lambda route, start, end: [Fare(route, start, Decimal("100"), True)], schedule)
        self.assertIn(KEY, result)
        self.assertFalse(diagnostic["complete"])


if __name__ == "__main__":
    unittest.main()
