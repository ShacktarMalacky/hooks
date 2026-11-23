import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import slack_to_issues as s2i

BASE_ENV = {
    "SLACK_BOT_TOKEN": "x",
    "GITHUB_REPOSITORY": "o/r",
    "GITHUB_TOKEN": "g",
    "SLACK_CHANNEL_ID": "C1",
    "SLACK_CHANNEL_NAME": "general",
    "ISSUE_LABELS": "slack",
}


class ParseCommandTests(unittest.TestCase):
    def test_close_variants(self):
        for text, expected in (
            ("cierra #12", ("close", 12)),
            ("Cerrar 12", ("close", 12)),
            ("close #12", ("close", 12)),
            ("Por favor cierra #3", ("close", 3)),
        ):
            self.assertEqual(s2i.parse_command(text), expected)

    def test_reopen_variants(self):
        for text, expected in (
            ("reabre #7", ("open", 7)),
            ("reopen 7", ("open", 7)),
            ("Reabre#9", ("open", 9)),
        ):
            self.assertEqual(s2i.parse_command(text), expected)

    def test_no_command(self):
        self.assertIsNone(s2i.parse_command("hola, esto no es un comando"))
        self.assertIsNone(s2i.parse_command("cierra la puerta"))
        self.assertIsNone(s2i.parse_command(""))


class ApplyCommandTests(unittest.TestCase):
    def test_close_issue(self):
        with mock.patch.object(s2i, "gh_set_issue_state", return_value={}) as patch_state, \
                mock.patch.object(s2i, "slack_post", return_value={}) as patch_post:
            result = s2i.apply_command(
                "close", 12, "Ana", "o/r", "g", "slack_tok", "C1", "1700.1"
            )
        self.assertTrue(result)
        patch_state.assert_called_once_with("o/r", "g", 12, "close")
        args, kwargs = patch_post.call_args
        self.assertEqual(kwargs.get("thread_ts"), "1700.1")

    def test_state_failure_returns_false(self):
        with mock.patch.object(s2i, "gh_set_issue_state", side_effect=RuntimeError("404")), \
                mock.patch.object(s2i, "slack_post") as patch_post:
            result = s2i.apply_command(
                "open", 99, "Ana", "o/r", "g", "slack_tok", "C1", "1700.1"
            )
        self.assertFalse(result)
        patch_post.assert_not_called()


class NotifyIssueCreatedTests(unittest.TestCase):
    def test_reacts_and_posts_link(self):
        issue = {"number": 5, "html_url": "https://github.com/o/r/issues/5", "title": "Algo"}
        with mock.patch.object(s2i, "slack_react", return_value={}) as patch_react, \
                mock.patch.object(s2i, "slack_post", return_value={}) as patch_post:
            s2i.notify_issue_created("tok", "C1", "1700.9", issue)
        patch_react.assert_called_once_with("tok", "C1", "1700.9", s2i.REACTION_EMOJI)
        text = patch_post.call_args[0][2]
        self.assertIn("#5", text)
        self.assertIn("https://github.com/o/r/issues/5", text)
        self.assertEqual(patch_post.call_args[1]["thread_ts"], "1700.9")

    def test_side_effect_failure_does_not_raise(self):
        with mock.patch.object(s2i, "slack_react", side_effect=RuntimeError("missing_scope")), \
                mock.patch.object(s2i, "slack_post", side_effect=RuntimeError("missing_scope")):
            s2i.notify_issue_created(
                "tok", "C1", "1700.9", {"number": 1, "html_url": "u", "title": "t"}
            )


class SyncThreadRepliesTests(unittest.TestCase):
    def test_replies_become_comments(self):
        state = {
            "issues": {"3": {"ts": "1700.5", "channel": "C1"}},
            "replies": {},
            "comments": {},
        }
        replies = {
            "ok": True,
            "messages": [
                {"type": "message", "ts": "1700.5", "text": "mensaje original", "user": "U1"},
                {"type": "message", "ts": "1700.8", "text": "respuesta importante", "user": "U2"},
                {"type": "message", "ts": "1700.9", "text": "del bot", "user": "U3", "bot_id": "B1"},
            ],
        }
        with mock.patch.object(s2i, "slack_get", return_value=replies), \
                mock.patch.object(s2i, "resolve_author", return_value="Ana"), \
                mock.patch.object(s2i, "gh_add_comment", return_value={}) as patch_comment:
            changed = s2i.sync_thread_replies(state, "o/r", "g", "tok", "C1")
        self.assertTrue(changed)
        patch_comment.assert_called_once()
        body = patch_comment.call_args[0][3]
        self.assertEqual(patch_comment.call_args[0][2], 3)
        self.assertIn("Ana (Slack)", body)
        self.assertIn("respuesta importante", body)
        self.assertEqual(state["replies"]["3"], "1700.9")

    def test_no_new_replies_keeps_state(self):
        state = {
            "issues": {"3": {"ts": "1700.5", "channel": "C1"}},
            "replies": {"3": "1700.8"},
            "comments": {},
        }
        with mock.patch.object(s2i, "slack_get", return_value={"ok": True, "messages": []}):
            changed = s2i.sync_thread_replies(state, "o/r", "g", "tok", "C1")
        self.assertFalse(changed)


class SyncIssueCommentsTests(unittest.TestCase):
    def test_human_comment_goes_to_thread_bot_skipped(self):
        state = {
            "issues": {"5": {"ts": "1700.5", "channel": "C1"}},
            "replies": {},
            "comments": {},
        }
        comments = [
            {"id": 10, "user": {"login": "ana", "type": "User"}, "body": "arreglado en main"},
            {"id": 11, "user": {"login": "github-actions[bot]", "type": "Bot"}, "body": "Ana (Slack): hola"},
            {"id": 12, "user": {"login": "bob", "type": "User"}, "body": "confirmo"},
        ]
        with mock.patch.object(s2i, "http_json", return_value=comments), \
                mock.patch.object(s2i, "slack_post", return_value={}) as patch_post:
            changed = s2i.sync_issue_comments(state, "o/r", "g", "tok", "C1")
        self.assertTrue(changed)
        self.assertEqual(patch_post.call_count, 2)
        first_text = patch_post.call_args_list[0][0][2]
        self.assertIn("ana", first_text)
        self.assertIn("arreglado en main", first_text)
        self.assertEqual(patch_post.call_args_list[0][1]["thread_ts"], "1700.5")
        self.assertEqual(state["comments"]["5"], 12)

    def test_no_comments_no_change(self):
        state = {
            "issues": {"5": {"ts": "1700.5", "channel": "C1"}},
            "replies": {},
            "comments": {},
        }
        with mock.patch.object(s2i, "http_json", return_value=[]):
            changed = s2i.sync_issue_comments(state, "o/r", "g", "tok", "C1")
        self.assertFalse(changed)


class FullFlowBidirectionalTests(unittest.TestCase):
    def test_command_message_does_not_create_issue(self):
        checkpoint = "/tmp/test_ckpt_cmd.json"
        threads = "/tmp/test_threads_cmd.json"
        for path in (checkpoint, threads):
            if os.path.exists(path):
                os.remove(path)
        env = dict(BASE_ENV, CHECKPOINT_FILE=checkpoint, THREADS_FILE=threads)
        messages = {
            "ok": True,
            "messages": [
                {"type": "message", "ts": "1700000010.0", "text": "cierra #4", "user": "U1"},
            ],
        }
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(s2i, "slack_get", return_value=messages), \
                    mock.patch.object(s2i, "resolve_author", return_value="Ana"), \
                    mock.patch.object(s2i, "apply_command", return_value=True) as patch_cmd, \
                    mock.patch.object(s2i, "create_issue") as patch_create, \
                    mock.patch.object(s2i, "sync_thread_replies", return_value=False), \
                    mock.patch.object(s2i, "sync_issue_comments", return_value=False):
                code = s2i.main()
        self.assertEqual(code, 0)
        patch_create.assert_not_called()
        patch_cmd.assert_called_once()
        with open(checkpoint, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["last_ts"], "1700000010.0")
        self.assertFalse(os.path.exists(threads))
        os.remove(checkpoint)

    def test_creation_records_thread_state_and_saves(self):
        checkpoint = "/tmp/test_ckpt_bidir.json"
        threads = "/tmp/test_threads_bidir.json"
        for path in (checkpoint, threads):
            if os.path.exists(path):
                os.remove(path)
        env = dict(BASE_ENV, CHECKPOINT_FILE=checkpoint, THREADS_FILE=threads)
        messages = {
            "ok": True,
            "messages": [
                {"type": "message", "ts": "1700000002.0", "text": "nuevo reporte", "user": "U1"},
            ],
        }
        issue = {"number": 7, "html_url": "https://github.com/o/r/issues/7", "title": "nuevo reporte"}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(s2i, "slack_get", return_value=messages), \
                    mock.patch.object(s2i, "resolve_author", return_value="Ana"), \
                    mock.patch.object(s2i, "issue_exists", return_value=False), \
                    mock.patch.object(s2i, "create_issue", return_value=issue), \
                    mock.patch.object(s2i, "notify_issue_created") as patch_notify, \
                    mock.patch.object(s2i, "sync_thread_replies", return_value=False), \
                    mock.patch.object(s2i, "sync_issue_comments", return_value=False):
                code = s2i.main()
        self.assertEqual(code, 0)
        patch_notify.assert_called_once()
        with open(threads, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["issues"]["7"], {"ts": "1700000002.0", "channel": "C1"})
        os.remove(checkpoint)
        os.remove(threads)


if __name__ == "__main__":
    unittest.main()
