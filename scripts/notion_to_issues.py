#!/usr/bin/env python3
"""Create GitHub issues from rows in a Notion database and sync status back.

Setup (all free):
- A Notion internal integration token (see README) stored as NOTION_TOKEN.
- A Notion database (title configured via NOTION_DATABASE_TITLE, default
  "Bugs") shared with the integration.

Behaviour:
- Rows whose status property equals the "new" value become GitHub issues.
- The row is then marked with the triage status and the issue URL, which also
  deduplicates future runs.
- Issues closed on GitHub flip the row status to the "done" value.
"""

import json
import os
import sys
import urllib.error
import urllib.request

NOTION_API = "https://api.notion.com/v1"
GITHUB_API = "https://api.github.com"
NOTION_VERSION = "2022-06-28"


def http_json(url, method="GET", token=None, body=None, headers=None):
    request = urllib.request.Request(url, data=None, method=method)
    if body is not None:
        request.data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {error.code} calling {url}: {detail}") from error


def notion_request(path, token, method="GET", body=None):
    headers = {"Notion-Version": NOTION_VERSION}
    result = http_json(f"{NOTION_API}/{path}", method=method, token=token, body=body, headers=headers)
    if result.get("object") == "error" or result.get("status") is not None:
        raise RuntimeError(f"Notion API {path} failed: {result.get('message') or result.get('code')}")
    return result


def plain_texts(richtext_list):
    return "".join(item.get("plain_text", "") for item in (richtext_list or [])).strip()


def find_database(token, title):
    result = notion_request(
        "search",
        token,
        method="POST",
        body={"filter": {"property": "object", "value": "database"}, "page_size": 100},
    )
    for database in result.get("results", []):
        titles = [t.get("plain_text", "").strip().lower() for t in database.get("title", [])]
        if title.lower() in titles:
            return database["id"]
    raise RuntimeError(
        f"No se encontró una base de datos de Notion con el título '{title}'. "
        "Compártela con la integración y revisa NOTION_DATABASE_TITLE."
    )


def query_database(token, database_id):
    results = []
    start_cursor = None
    while True:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        page = notion_request(f"databases/{database_id}/query", token, method="POST", body=body)
        results.extend(page.get("results", []))
        if not page.get("has_more"):
            return results
        start_cursor = page.get("next_cursor")


def property_status_value(properties, prop_name):
    prop = properties.get(prop_name)
    if not prop:
        return None
    if prop.get("type") in ("select", "status"):
        option = prop.get(prop["type"])
        return option.get("name") if option else None
    if prop.get("type") == "checkbox":
        return prop.get("checkbox")
    return None


def set_property_status(properties, prop_name, value):
    prop = properties.get(prop_name)
    if not prop or prop.get("type") not in ("select", "status"):
        return {"properties": {prop_name: {"select": {"name": value}}}}
    prop_type = prop["type"]
    return {"properties": {prop_name: {prop_type: {"name": value}}}}


def property_issue_url(properties, prop_name):
    prop = properties.get(prop_name)
    if not prop or prop.get("type") not in ("rich_text", "url"):
        return None
    if prop["type"] == "url":
        return prop.get("url")
    return plain_texts(prop.get("rich_text", []))


def make_issue_payload(page, status_prop, title_prop):
    properties = page.get("properties", {})
    title = plain_texts((properties.get(title_prop) or {}).get("title", [])) or "(sin título)"
    lines = [
        f"Reportado desde Notion: [fila]({page.get('url')})",
        f"[notion-row:{page['id']}]",
    ]
    for name, prop in properties.items():
        if name in (title_prop, status_prop):
            continue
        ptype = prop.get("type")
        if ptype in ("select", "status"):
            option = prop.get(ptype) or {}
            if option.get("name"):
                lines.append(f"- **{name}:** {option['name']}")
        elif ptype == "rich_text" and plain_texts(prop.get("rich_text", [])):
            lines.append(f"- **{name}:** {plain_texts(prop['rich_text'])}")
        elif ptype == "url" and prop.get("url"):
            lines.append(f"- **{name}:** {prop['url']}")
        elif ptype == "number" and prop.get("number") is not None:
            lines.append(f"- **{name}:** {prop['number']}")
    return title, "\n".join(lines)


def create_issue(repo, token, title, body, labels):
    return http_json(
        f"{GITHUB_API}/repos/{repo}/issues",
        method="POST",
        token=token,
        body={"title": title, "body": body, "labels": labels},
    )


def get_issue_state(repo, token, number):
    result = http_json(f"{GITHUB_API}/repos/{repo}/issues/{number}", token=token)
    return result.get("state", "open")


def issue_exists_for_row(repo, token, page_id):
    import urllib.parse

    query = urllib.parse.quote(f'repo:{repo} is:issue "notion-row:{page_id}"')
    result = http_json(f"{GITHUB_API}/search/issues?q={query}&per_page=1", token=token)
    return result.get("total_count", 0) > 0


def issue_number_from_url(url):
    if not url or "/issues/" not in url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def main():
    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    if not notion_token:
        print("WARNING: NOTION_TOKEN no esta configurado; no se hace nada.")
        return 0

    repo = os.environ["GITHUB_REPOSITORY"]
    github_token = os.environ["GITHUB_TOKEN"]
    db_title = os.environ.get("NOTION_DATABASE_TITLE", "Bugs")
    title_prop = os.environ.get("NOTION_TITLE_PROP", "Name")
    status_prop = os.environ.get("NOTION_STATUS_PROP", "Estado")
    issue_prop = os.environ.get("NOTION_ISSUE_PROP", "Issue")
    status_new = os.environ.get("NOTION_STATUS_NEW", "Reportar")
    status_triage = os.environ.get("NOTION_STATUS_TRIAGE", "En GitHub")
    status_done = os.environ.get("NOTION_STATUS_DONE", "Hecho")
    labels = [label for label in os.environ.get("ISSUE_LABELS", "notion").split(",") if label]

    database_id = find_database(notion_token, db_title)
    rows = query_database(notion_token, database_id)
    print(f"Filas leidas de Notion: {len(rows)}")

    created = 0
    updated = 0
    for page in rows:
        page_id = page["id"]
        properties = page.get("properties", {})
        status = property_status_value(properties, status_prop)
        issue_url = property_issue_url(properties, issue_prop)

        if issue_url:
            number = issue_number_from_url(issue_url)
            if number and status != status_done:
                try:
                    state = get_issue_state(repo, github_token, number)
                except Exception as error:
                    print(f"  aviso: no se pudo leer el issue #{number}: {error}")
                    continue
                if state == "closed":
                    notion_request(
                        f"pages/{page_id}",
                        notion_token,
                        method="PATCH",
                        body=set_property_status(properties, status_prop, status_done),
                    )
                    print(f"  fila {page_id}: issue #{number} cerrado -> estado '{status_done}'")
                    updated += 1
            continue

        if status != status_new:
            continue
        title, body = make_issue_payload(page, status_prop, title_prop)
        if issue_exists_for_row(repo, github_token, page_id):
            print(f"  fila {page_id}: ya existe un issue, se omiten pasos de creacion")
            continue
        issue = create_issue(repo, github_token, title, body, labels)
        issue_url_new = issue.get("html_url", "")
        print(f"  fila {page_id}: issue creado -> {issue_url_new}")
        created += 1
        patch = set_property_status(properties, status_prop, status_triage)
        patch["properties"][issue_prop] = {
            "rich_text": [{"text": {"content": issue_url_new}}]
        }
        notion_request(f"pages/{page_id}", notion_token, method="PATCH", body=patch)

    print(f"Issues creados: {created}; filas actualizadas: {updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
