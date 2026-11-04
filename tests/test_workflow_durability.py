"""Regression guard: scheduled runs must persist the outbox across ephemeral runners."""
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ryanair-monitor.yml"


class WorkflowDurabilityTests(unittest.TestCase):
    def test_pending_alerts_in_git_commit_and_change_detection(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        lines = workflow.splitlines()
        status = [line for line in lines if "git status --porcelain --" in line]
        staging = [line for line in lines if "git add --" in line]
        self.assertEqual(len(status), 1)
        self.assertEqual(len(staging), 1)
        for line in status + staging:
            for path in ("state/history.json", "state/pending_alerts.json",
                         "state/latest_verified.json", "state/last_run.json"):
                self.assertIn(path, line)

    def test_scheduled_monitor_is_serialized_and_calendar_writes_disabled(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("group: ryanair-pa-ap-monitor", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn('GOOGLE_CALENDAR_SYNC_ENABLED: "0"', workflow)


if __name__ == "__main__":
    unittest.main()
