"""Isolated cross-process restart test. No Google Calendar writes or real state changes."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OutboxRestartTests(unittest.TestCase):
    def test_outbox_survives_new_python_process_and_ack_requires_all_calendars(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "history.json"
            first = """from pathlib import Path
from monitor import save_pending, pending_path
p = Path(__import__('sys').argv[1])
save_pending(pending_path(p), [{'change_id':'restart-test-1','route':'PA','date':'2026-11-06',
 'previous_pln':'200','current_pln':'180'}])
"""
            second = """from pathlib import Path
from monitor import load_pending, pending_path, acknowledge_alerts, REQUIRED_CALENDARS
p = Path(__import__('sys').argv[1])
assert len(load_pending(pending_path(p))) == 1
assert acknowledge_alerts(p, {'restart-test-1': {REQUIRED_CALENDARS[0]: 'event-1'}},
                          verifier=lambda *args: True) == []
assert len(load_pending(pending_path(p))) == 1
receipts = {'restart-test-1': {c: 'event-'+str(i) for i,c in enumerate(REQUIRED_CALENDARS)}}
assert acknowledge_alerts(p, receipts, verifier=lambda *args: True) == ['restart-test-1']
assert load_pending(pending_path(p)) == []
"""
            for code in (first, second):
                subprocess.run([sys.executable, "-c", code, str(state)], cwd=ROOT,
                               check=True, capture_output=True, text=True)
            self.assertEqual(json.loads((Path(directory) / "pending_alerts.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()
