import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import slack_to_issues as s2i


class ClassifyTests(unittest.TestCase):
    def test_bug(self):
        self.assertIn("bug", s2i.classify("Tengo un Error al abrir el juego"))
        self.assertIn("bug", s2i.classify("la app se Rompe en android"))

    def test_accent_insensitive(self):
        self.assertIn("bug", s2i.classify("Excepcion no controlada"))

    def test_pregunta(self):
        self.assertIn("pregunta", s2i.classify("¿se puede cambiar el color?"))

    def test_mejora(self):
        self.assertIn("mejora", s2i.classify("Sugerencia: agregar un modo oscuro"))

    def test_prioridad(self):
        labels = s2i.classify("Urgente: nada funciona")
        self.assertIn("prioridad:alta", labels)

    def test_no_labels(self):
        self.assertEqual(s2i.classify("hola a todos"), [])


class EnsureLabelsTests(unittest.TestCase):
    def test_creates_only_missing(self):
        def fake(url, method="GET", token=None, body=None, headers=None):
            if url.endswith("per_page=100"):
                return [{"name": "slack"}, {"name": "bug"}]
            return {}

        with mock.patch.object(s2i, "http_json", side_effect=fake) as json_mock:
            s2i.ensure_labels("o/r", "t", ["slack", "bug", "mejora"])
        creation_calls = [
            call for call in json_mock.call_args_list if call[1].get("method") == "POST"
        ]
        self.assertEqual(len(creation_calls), 1)
        self.assertEqual(creation_calls[0][1]["body"], {"name": "mejora"})

    def test_read_failure_continues(self):
        with mock.patch.object(s2i, "http_json", side_effect=RuntimeError("401")):
            s2i.ensure_labels("o/r", "t", ["bug"])


class FindSimilarIssueTests(unittest.TestCase):
    def test_finds_similar(self):
        open_issues = [
            {"number": 2, "title": "Error al abrir el juego",
             "html_url": "https://github.com/o/r/issues/2"},
        ]
        with mock.patch.object(s2i, "fetch_open_issues", return_value=open_issues):
            match = s2i.find_similar_issue("o/r", "t", "Error al abrir el juego", 0.8)
        self.assertEqual(match["number"], 2)

    def test_below_threshold_returns_none(self):
        open_issues = [{"number": 2, "title": "Refactor del motor de render"}]
        with mock.patch.object(s2i, "fetch_open_issues", return_value=open_issues):
            self.assertIsNone(
                s2i.find_similar_issue("o/r", "t", "Error al abrir el juego", 0.8)
            )

    def test_skips_pull_requests(self):
        open_issues = [
            {"number": 3, "title": "Error al abrir el juego", "pull_request": {"url": "p"}}
        ]
        with mock.patch.object(s2i, "fetch_open_issues", return_value=open_issues):
            self.assertIsNone(
                s2i.find_similar_issue("o/r", "t", "Error al abrir el juego", 0.8)
            )

    def test_fetch_failure_returns_none(self):
        with mock.patch.object(s2i, "fetch_open_issues", side_effect=RuntimeError("401")):
            self.assertIsNone(s2i.find_similar_issue("o/r", "t", "lo que sea", 0.8))


class ManageStaleTests(unittest.TestCase):
    NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)

    def make_issue(self, number, updated_at):
        return {
            "number": number,
            "body": "[slack-ts:1.2]",
            "state": "open",
            "updated_at": updated_at,
            "pull_request": None,
            "html_url": f"https://github.com/o/r/issues/{number}",
        }

    def test_recent_issue_untouched(self):
        issue = self.make_issue(5, "2026-02-25T00:00:00Z")
        with mock.patch.object(s2i, "fetch_open_issues", return_value=[issue]):
            changed = s2i.manage_stale("o/r", "t", 30, 7, now=self.NOW)
        self.assertFalse(changed)

    def test_old_issue_gets_warning(self):
        issue = self.make_issue(5, "2026-01-01T00:00:00Z")
        with mock.patch.object(s2i, "fetch_open_issues", return_value=[issue]), \
                mock.patch.object(s2i, "http_json", return_value=[]), \
                mock.patch.object(s2i, "gh_add_comment", return_value={}) as comment, \
                mock.patch.object(s2i, "gh_set_issue_state") as close:
            changed = s2i.manage_stale("o/r", "t", 30, 7, now=self.NOW)
        self.assertTrue(changed)
        comment.assert_called_once()
        self.assertIn("[stale:2026-03-01]", comment.call_args[0][3])
        close.assert_not_called()

    def test_old_warning_closes_issue(self):
        issue = self.make_issue(5, "2026-01-01T00:00:00Z")
        comments = [
            {"body": "aviso [stale:2026-02-01]", "id": 1, "user": {"login": "x"}}
        ]
        with mock.patch.object(s2i, "fetch_open_issues", return_value=[issue]), \
                mock.patch.object(s2i, "http_json", return_value=comments), \
                mock.patch.object(s2i, "gh_add_comment") as comment, \
                mock.patch.object(s2i, "gh_set_issue_state", return_value={}) as close:
            changed = s2i.manage_stale("o/r", "t", 30, 7, now=self.NOW)
        self.assertTrue(changed)
        close.assert_called_once_with("o/r", "t", 5, "close")
        comment.assert_not_called()

    def test_recent_warning_not_closed(self):
        issue = self.make_issue(5, "2026-01-01T00:00:00Z")
        comments = [{"body": "aviso [stale:2026-02-27]", "id": 1}]
        with mock.patch.object(s2i, "fetch_open_issues", return_value=[issue]), \
                mock.patch.object(s2i, "http_json", return_value=comments), \
                mock.patch.object(s2i, "gh_set_issue_state") as close:
            changed = s2i.manage_stale("o/r", "t", 30, 7, now=self.NOW)
        self.assertFalse(changed)
        close.assert_not_called()

    def test_ignores_foreign_issues(self):
        issue = self.make_issue(5, "2026-01-01T00:00:00Z")
        issue["body"] = "issue manual sin marcador"
        with mock.patch.object(s2i, "fetch_open_issues", return_value=[issue]):
            changed = s2i.manage_stale("o/r", "t", 30, 7, now=self.NOW)
        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
