import unittest
from unittest import mock

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import slack_to_issues as s2i


class HourBucketTests(unittest.TestCase):
    def test_changes_with_hour(self):
        from datetime import datetime, timezone

        first = s2i.hour_bucket(datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
        second = s2i.hour_bucket(datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc))
        self.assertNotEqual(first, second)

    def test_get_spam_counts_resets_new_hour(self):
        state = {"spam": {"hour": "vieja", "counts": {"U1": 3}}}
        counts = s2i.get_spam_counts(state, now=__import__("datetime").datetime(
            2026, 1, 1, 10, 0, tzinfo=__import__("datetime").timezone.utc
        ))
        self.assertEqual(counts, {})


class SpamAllowsTests(unittest.TestCase):
    def test_allows_below_limit(self):
        self.assertTrue(s2i.spam_allows({"U1": 2}, "U1", 5))
        self.assertTrue(s2i.spam_allows({}, "U1", 5))

    def test_blocks_at_limit(self):
        self.assertFalse(s2i.spam_allows({"U1": 5}, "U1", 5))

    def test_zero_disables(self):
        counts = {"U1": 100}
        self.assertTrue(s2i.spam_allows(counts, "U1", 0))


class SpamFlowTests(unittest.TestCase):
    def test_flood_is_blocked_and_notified(self):
        checkpoint = "/tmp/test_ckpt_spam.json"
        threads = "/tmp/test_threads_spam.json"
        for path in (checkpoint, threads):
            if os.path.exists(path):
                os.remove(path)
        messages = {
            "ok": True,
            "messages": [
                {"type": "message", "ts": f"1700{i}.0", "text": f"reporte {i}", "user": "U1"}
                for i in range(1, 8)
            ],
        }
        env = {
            "SLACK_BOT_TOKEN": "s",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_TOKEN": "g",
            "SLACK_CHANNEL_ID": "C1",
            "CHECKPOINT_FILE": checkpoint,
            "THREADS_FILE": threads,
            "SPAM_MAX_PER_HOUR": "5",
        }
        notifications = []

        with mock.patch.dict(os.environ, env):
            with mock.patch.object(s2i, "slack_get", return_value=messages), \
                    mock.patch.object(s2i, "resolve_author", return_value="Ana"), \
                    mock.patch.object(s2i, "issue_exists", return_value=False), \
                    mock.patch.object(s2i, "find_similar_issue", return_value=None), \
                    mock.patch.object(s2i, "ensure_labels", return_value=None), \
                    mock.patch.object(s2i, "manage_stale", return_value=False), \
                    mock.patch.object(s2i, "create_issue", return_value={}), \
                    mock.patch.object(s2i, "notify_issue_created"), \
                    mock.patch.object(s2i, "slack_post") as post, \
                    mock.patch.object(s2i, "sync_thread_replies", return_value=False), \
                    mock.patch.object(s2i, "sync_issue_comments", return_value=False):
                code = s2i.main()
        self.assertEqual(code, 0)
        blocked = [
            call for call in post.call_args_list if "límite" in call[0][2]
        ]
        self.assertEqual(len(blocked), 2)

        for path in (checkpoint, threads):
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
