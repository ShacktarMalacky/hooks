import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import slack_to_issues as s2i


class IsIssueWorthyTests(unittest.TestCase):
    def test_plain_message(self):
        self.assertTrue(s2i.is_issue_worthy({"type": "message", "text": "hola"}))

    def test_skips_subtypes(self):
        self.assertFalse(
            s2i.is_issue_worthy(
                {"type": "message", "subtype": "message_changed", "text": "x"}
            )
        )

    def test_skips_bots(self):
        self.assertFalse(
            s2i.is_issue_worthy({"type": "message", "text": "x", "bot_id": "B1"})
        )

    def test_skips_empty_text(self):
        self.assertFalse(s2i.is_issue_worthy({"type": "message", "text": "  "}))


class MakeTitleTests(unittest.TestCase):
    def test_first_line(self):
        self.assertEqual(s2i.make_title("linea uno\nlinea dos"), "linea uno")

    def test_truncates(self):
        title = s2i.make_title("a" * 200)
        self.assertLessEqual(len(title), 70)
        self.assertTrue(title.endswith("…"))


class KeywordTests(unittest.TestCase):
    def test_all_when_no_keywords(self):
        self.assertTrue(s2i.matches_keywords("cualquier cosa", []))

    def test_requires_all_keywords(self):
        self.assertTrue(s2i.matches_keywords("Bug critico en login", ["bug"]))
        self.assertFalse(s2i.matches_keywords("Bug critico", ["bug", "login"]))


class IssueBodyTests(unittest.TestCase):
    def test_body_contains_marker_and_link(self):
        message = {"ts": "1700000000.000100", "text": "hola mundo"}
        body = s2i.format_issue_body(message, "Ana", "C123", "general")
        self.assertIn("[slack-ts:1700000000.000100]", body)
        self.assertIn("app_redirect?channel=C123&message_ts=1700000000.000100", body)
        self.assertIn("Ana", body)
        self.assertIn("hola mundo", body)


class CheckpointTests(unittest.TestCase):
    def test_roundtrip(self):
        path = os.path.join(os.path.dirname(__file__), "tmp_checkpoint_test.json")
        self.assertEqual(s2i.load_checkpoint(path), "0")
        s2i.save_checkpoint(path, "1700.5")
        self.assertEqual(s2i.load_checkpoint(path), "1700.5")
        os.remove(path)

    def test_corrupt_file_falls_back(self):
        path = os.path.join(os.path.dirname(__file__), "tmp_checkpoint_bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{no json")
        self.assertEqual(s2i.load_checkpoint(path), "0")
        os.remove(path)


class MainFlowTests(unittest.TestCase):
    def test_creates_issue_and_checkpoint(self):
        checkpoint = os.path.join(os.path.dirname(__file__), "tmp_ckpt_flow.json")
        if os.path.exists(checkpoint):
            os.remove(checkpoint)
        messages = {
            "ok": True,
            "messages": [
                {"type": "message", "ts": "1700000005.0", "text": "segunda", "user": "U1"},
                {"type": "message", "ts": "1700000001.0", "text": "primera", "user": "U1"},
                {"type": "message", "ts": "1700000002.0", "text": "bot", "bot_id": "B1"},
            ],
        }
        with mock.patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "x",
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_TOKEN": "g",
                "SLACK_CHANNEL_ID": "C1",
                "CHECKPOINT_FILE": checkpoint,
                "ISSUE_LABELS": "slack",
            },
        ):
            with mock.patch.object(s2i, "slack_get", return_value=messages), mock.patch.object(
                s2i, "resolve_author", return_value="Ana"
            ), mock.patch.object(s2i, "issue_exists", return_value=False), mock.patch.object(
                s2i,
                "create_issue",
                return_value={"html_url": "https://github.com/o/r/issues/1"},
            ) as create:
                code = s2i.main()
        self.assertEqual(code, 0)
        self.assertEqual(create.call_count, 2)
        with open(checkpoint, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["last_ts"], "1700000005.0")
        os.remove(checkpoint)

    def test_skips_existing_issues(self):
        checkpoint = os.path.join(os.path.dirname(__file__), "tmp_ckpt_dedup.json")
        if os.path.exists(checkpoint):
            os.remove(checkpoint)
        messages = {
            "ok": True,
            "messages": [
                {"type": "message", "ts": "1700000009.0", "text": "ya visto", "user": "U1"}
            ],
        }
        with mock.patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "x",
                "GITHUB_REPOSITORY": "o/r",
                "GITHUB_TOKEN": "g",
                "SLACK_CHANNEL_ID": "C1",
                "CHECKPOINT_FILE": checkpoint,
            },
        ):
            with mock.patch.object(s2i, "slack_get", return_value=messages), mock.patch.object(
                s2i, "resolve_author", return_value="A"
            ), mock.patch.object(s2i, "issue_exists", return_value=True), mock.patch.object(
                s2i, "create_issue"
            ) as create:
                code = s2i.main()
        self.assertEqual(code, 0)
        create.assert_not_called()
        with open(checkpoint, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["last_ts"], "1700000009.0")
        os.remove(checkpoint)

    def test_missing_token_exits_cleanly(self):
        with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": ""}):
            self.assertEqual(s2i.main(), 0)


if __name__ == "__main__":
    unittest.main()
