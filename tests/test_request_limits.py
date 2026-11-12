import io
import json
import unittest
from urllib.error import HTTPError

from request_limits import RequestPacer
from calendar_sync import GoogleCalendarApi, CalendarError


class Clock:
    def __init__(self):
        self.now = 0.0
    def time(self):
        return self.now
    def sleep(self, delay):
        self.now += delay


class LimitsTests(unittest.TestCase):
    def test_weighted_patches_stay_below_minute_quota(self):
        clock = Clock()
        pacer = RequestPacer(5, clock.time, clock.sleep)
        starts = []
        for _ in range(601):
            for units in (1, 3, 1):
                pacer.wait(units)
                starts.append((clock.now, units))
        self.assertGreater(clock.now, 600)
        self.assertEqual(pacer.units, 3005)
        # Check every request-aligned rolling minute, including startup.
        self.assertLessEqual(max(sum(u for t, u in starts if start <= t < start + 60)
                                 for start, _ in starts), 303)

    def test_oauth_error_does_not_expose_response_secrets(self):
        def opener(req, timeout):
            body = json.dumps({"error": "invalid_grant", "error_description": "SECRET"}).encode()
            raise HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(body))
        api = GoogleCalendarApi("client", "secret", "refresh", opener)
        with self.assertRaises(CalendarError) as error:
            api.refresh()
        self.assertIn("invalid_grant", str(error.exception))
        self.assertNotIn("SECRET", str(error.exception))

    def test_rate_limit_403_retries_but_permission_403_does_not(self):
        for reason, expected_calls in [("userRateLimitExceeded", 2), ("forbidden", 1)]:
            clock = Clock()
            calls = []
            def opener(req, timeout):
                calls.append(req)
                if len(calls) == 1:
                    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
                    raise HTTPError(req.full_url, 403, "bad", {}, io.BytesIO(body))
                return io.BytesIO(b'{"id":"ok"}')
            api = GoogleCalendarApi("client", "secret", "refresh", opener,
                                    RequestPacer(5, clock.time, clock.sleep), clock.sleep)
            api.token, api.expires = "token", float("inf")
            if expected_calls == 2:
                self.assertEqual(api.get("ok"), {"id": "ok"})
            else:
                with self.assertRaises(CalendarError):
                    api.get("ok")
            self.assertEqual(len(calls), expected_calls)

    def test_post_is_not_blindly_retried_after_transient_failure(self):
        calls = []
        def opener(req, timeout):
            calls.append(req)
            raise HTTPError(req.full_url, 503, "unavailable", {}, io.BytesIO(b'{}'))
        api = GoogleCalendarApi("client", "secret", "refresh", opener)
        api.token, api.expires = "token", float("inf")
        with self.assertRaises(CalendarError):
            api.insert("stable-id", {})
        self.assertEqual(len(calls), 1)
