import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import github_digest as gd
import healthcheck as hc
import setup_labels as sl


class BuildDigestTests(unittest.TestCase):
    def test_full_digest(self):
        created = [
            {"number": 1, "title": "Error de login", "labels": [{"name": "bug"}]},
            {"number": 2, "title": "Mejorar menú", "labels": [{"name": "mejora"}]},
        ]
        closed = [{"number": 3, "title": "Viejo bug", "labels": [{"name": "bug"}]}]
        text = gd.build_digest(created, closed, 5, "últimos 7 días")
        self.assertIn("Abiertos en el periodo:* 2", text)
        self.assertIn("#1 — Error de login", text)
        self.assertIn("Cerrados en el periodo:* 1", text)
        self.assertIn("bug (2)", text)
        self.assertIn("Abiertos ahora mismo:* 5", text)

    def test_truncates_long_lists(self):
        created = [
            {"number": i, "title": f"issue {i}", "labels": []} for i in range(1, 15)
        ]
        text = gd.build_digest(created, [], 0, "últimos 7 días")
        self.assertIn("… y 4 más", text)
        self.assertNotIn("#14", text)


class DigestMainTests(unittest.TestCase):
    def test_missing_token_exits_cleanly(self):
        with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "", "GITHUB_TOKEN": ""}):
            self.assertEqual(gd.main(), 0)

    def test_posts_digest(self):
        search_results = [
            ([{"number": 1, "title": "abc", "labels": []}], 6),
            ([{"number": 2, "title": "def", "labels": []}], 2),
            ([], 5),
        ]
        with mock.patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "s",
                "GITHUB_TOKEN": "g",
                "GITHUB_REPOSITORY": "o/r",
                "SLACK_CHANNEL_ID": "C1",
            },
        ):
            with mock.patch.object(
                gd, "search_issues", side_effect=search_results
            ) as search, mock.patch.object(gd, "slack_post", return_value={}) as post:
                code = gd.main()
        self.assertEqual(code, 0)
        self.assertEqual(search.call_count, 3)
        args = post.call_args[0]
        self.assertEqual(args[1], "C1")
        self.assertIn("Resumen de issues", args[2])


class HealthcheckTests(unittest.TestCase):
    def test_report_with_failures(self):
        results = [("GitHub API", True, "ok"), ("Slack", False, "sin SLACK_BOT_TOKEN")]
        report = hc.render_report(results)
        self.assertIn("1/2 comprobaciones", report)
        self.assertIn(":white_check_mark:", report)
        self.assertIn(":x:", report)

    def test_check_labels_reports_missing(self):
        with mock.patch.object(
            hc,
            "http_json",
            return_value=[{"name": "bug"}, {"name": "pregunta"}],
        ):
            with self.assertRaises(RuntimeError) as ctx:
                hc.check_labels("o/r", "t")
        self.assertIn("mejora", str(ctx.exception))

    def test_check_labels_ok(self):
        with mock.patch.object(
            hc,
            "http_json",
            return_value=[{"name": name} for name in hc.REQUIRED_LABELS],
        ):
            detail = hc.check_labels("o/r", "t")
        self.assertIn("todas las etiquetas presentes", detail)

    def test_main_never_raises_and_returns_zero(self):
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}, clear=False):
            with mock.patch.object(hc, "collect_checks", return_value=[]):
                self.assertEqual(hc.main(), 0)


class SetupLabelsTests(unittest.TestCase):
    def test_creates_missing_only(self):
        def fake(url, method="GET", token=None, body=None):
            if url.endswith("per_page=100"):
                return [{"name": "bug"}]
            return {}

        with mock.patch.dict(
            os.environ, {"GITHUB_TOKEN": "g", "GITHUB_REPOSITORY": "o/r"}
        ):
            with mock.patch.object(sl, "http_json", side_effect=fake) as json_mock:
                code = sl.main()
        self.assertEqual(code, 0)
        posts = [
            call for call in json_mock.call_args_list if call[1].get("method") == "POST"
        ]
        self.assertEqual(len(posts), len(sl.DEFAULT_LABELS) - 1)
        created_names = {call[1]["body"]["name"] for call in posts}
        self.assertEqual(created_names, {"slack", "notion", "mejora", "pregunta", "prioridad:alta"})

    def test_missing_env_exits_cleanly(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPOSITORY": ""}):
            self.assertEqual(sl.main(), 0)
