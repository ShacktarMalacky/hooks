import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import notion_to_issues as n2i


def make_row(page_id="row-1", title="Bug en el juego", status="Reportar", issue_url=None):
    properties = {
        "Name": {"type": "title", "title": [{"plain_text": title}]},
        "Estado": {"type": "status", "status": {"name": status}},
        "Issue": (
            {"type": "rich_text", "rich_text": [{"plain_text": issue_url}]}
            if issue_url
            else {"type": "rich_text", "rich_text": []}
        ),
        "Prioridad": {
            "type": "select",
            "select": {"name": "alta"} if status else None,
        },
        "Version": {"type": "rich_text", "rich_text": [{"plain_text": "7.0"}]},
        "Captura": {"type": "url", "url": "https://img.example/1.png"},
    }
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "properties": properties,
    }


ENV = {
    "NOTION_TOKEN": "nt",
    "GITHUB_REPOSITORY": "o/r",
    "GITHUB_TOKEN": "g",
}


class FindDatabaseTests(unittest.TestCase):
    def test_finds_by_title(self):
        databases = {
            "object": "list",
            "results": [
                {"id": "db-1", "title": [{"plain_text": "Otra"}]},
                {"id": "db-2", "title": [{"plain_text": "Bugs"}]},
            ],
        }
        with mock.patch.object(n2i, "notion_request", return_value=databases):
            self.assertEqual(n2i.find_database("token", "Bugs"), "db-2")

    def test_missing_database_raises(self):
        with mock.patch.object(n2i, "notion_request", return_value={"results": []}):
            with self.assertRaises(RuntimeError):
                n2i.find_database("token", "Bugs")


class PropertyTests(unittest.TestCase):
    def test_status_select_and_status(self):
        for ptype in ("select", "status"):
            self.assertEqual(
                n2i.property_status_value(
                    {"Estado": {"type": ptype, ptype: {"name": "Reportar"}}}, "Estado"
                ),
                "Reportar",
            )

    def test_missing_property(self):
        self.assertIsNone(n2i.property_status_value({}, "Estado"))

    def test_issue_url_rich_text(self):
        self.assertEqual(
            n2i.property_issue_url(
                {"Issue": {"type": "rich_text", "rich_text": [{"plain_text": "u"}]}}, "Issue"
            ),
            "u",
        )

    def test_issue_number_from_url(self):
        self.assertEqual(
            n2i.issue_number_from_url("https://github.com/o/r/issues/42/"), 42
        )
        self.assertIsNone(n2i.issue_number_from_url("https://notion.so/x"))


class MakeIssuePayloadTests(unittest.TestCase):
    def test_payload_includes_marker_and_props(self):
        title, body = n2i.make_issue_payload(make_row(), "Estado", "Name")
        self.assertEqual(title, "Bug en el juego")
        self.assertIn("notion-row:row-1", body)
        self.assertIn("**Prioridad:** alta", body)
        self.assertIn("**Version:** 7.0", body)
        self.assertIn("**Captura:** https://img.example/1.png", body)
        self.assertNotIn("**Estado:**", body)


class MainFlowTests(unittest.TestCase):
    def _run(self, pages):
        with mock.patch.dict(os.environ, ENV):
            with mock.patch.object(
                n2i, "find_database", return_value="db-1"
            ), mock.patch.object(
                n2i, "query_database", return_value=pages
            ), mock.patch.object(
                n2i, "issue_exists_for_row", return_value=False
            ), mock.patch.object(
                n2i,
                "create_issue",
                return_value={"html_url": "https://github.com/o/r/issues/9"},
            ) as create, mock.patch.object(
                n2i, "notion_request", return_value={}
            ) as patch_page, mock.patch.object(
                n2i, "get_issue_state", return_value="open"
            ):
                code = n2i.main()
        return code, create, patch_page

    def test_creates_issue_and_marks_row(self):
        code, create, patch_page = self._run([make_row()])
        self.assertEqual(code, 0)
        create.assert_called_once()
        patch_page.assert_called_once()
        patch_body = patch_page.call_args[1]["body"]
        self.assertEqual(patch_body["properties"]["Estado"]["status"]["name"], "En GitHub")
        issue_text = patch_body["properties"]["Issue"]["rich_text"]
        self.assertEqual(issue_text[0]["text"]["content"], "https://github.com/o/r/issues/9")

    def test_skips_rows_with_issue_url_and_open_issue(self):
        code, create, patch_page = self._run([make_row(issue_url="https://github.com/o/r/issues/3")])
        self.assertEqual(code, 0)
        create.assert_not_called()
        patch_page.assert_not_called()

    def test_closed_issue_sets_done_status(self):
        with mock.patch.dict(os.environ, ENV):
            with mock.patch.object(n2i, "find_database", return_value="db-1"), \
                    mock.patch.object(n2i, "query_database", return_value=[
                        make_row(status="En GitHub", issue_url="https://github.com/o/r/issues/3", page_id="row-2")
                    ]), \
                    mock.patch.object(n2i, "get_issue_state", return_value="closed"), \
                    mock.patch.object(n2i, "notion_request", return_value={}) as patch_page:
                code = n2i.main()
        self.assertEqual(code, 0)
        patch_body = patch_page.call_args[1]["body"]
        self.assertEqual(patch_body["properties"]["Estado"]["status"]["name"], "Hecho")

    def test_missing_token_exits_cleanly(self):
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": ""}):
            self.assertEqual(n2i.main(), 0)


if __name__ == "__main__":
    unittest.main()
