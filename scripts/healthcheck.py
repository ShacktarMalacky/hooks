#!/usr/bin/env python3
"""Diagnose the repository automation setup: tokens, scopes and labels.

Runs as a GitHub Action (manual dispatch) or locally with GITHUB_TOKEN set.
Writes a report to stdout and to $GITHUB_STEP_SUMMARY when present.
"""

import json
import os
import sys
import urllib.error
import urllib.request

SLACK_API = "https://slack.com/api"
NOTION_API = "https://api.notion.com/v1"
GITHUB_API = "https://api.github.com"
NOTION_VERSION = "2022-06-28"

REQUIRED_LABELS = ["slack", "notion", "bug", "mejora", "pregunta", "prioridad:alta"]


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
        detail = error.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def check_slack(token):
    result = http_json(f"{SLACK_API}/auth.test", method="POST", token=token)
    if not result.get("ok"):
        raise RuntimeError(result.get("error"))
    team = result.get("team") or "?"
    user = result.get("user") or "?"
    return f"conectado como {user} en el workspace {team}"


def check_notion(token):
    result = http_json(
        f"{NOTION_API}/users/me",
        token=token,
        headers={"Notion-Version": NOTION_VERSION},
    )
    bot = result.get("name") or "bot"
    return f"token válido, integración: {bot}"


def check_github(repo, token):
    result = http_json(f"{GITHUB_API}/repos/{repo}", token=token)
    return f"acceso a {result.get('full_name', repo)} (visibilidad: {result.get('visibility', '?')})"


def check_labels(repo, token, required=None):
    required = required or REQUIRED_LABELS
    labels = http_json(f"{GITHUB_API}/repos/{repo}/labels?per_page=100", token=token)
    existing = {label["name"] for label in labels}
    missing = [label for label in required if label not in existing]
    if missing:
        raise RuntimeError(f"faltan etiquetas: {', '.join(missing)} (usa setup_labels.py)")
    return f"todas las etiquetas presentes ({len(existing)} en total)"


def missing_env_error(name):
    def fail():
        raise RuntimeError(f"sin {name}")
    return fail


def collect_checks():
    checks = []
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if github_token and repo:
        checks.append(("GitHub API", lambda: check_github(repo, github_token)))
    else:
        checks.append(("GitHub API", missing_env_error("GITHUB_TOKEN o GITHUB_REPOSITORY")))
    if slack_token:
        checks.append(("Slack", lambda: check_slack(slack_token)))
    else:
        checks.append(("Slack", missing_env_error("SLACK_BOT_TOKEN")))
    if notion_token:
        checks.append(("Notion", lambda: check_notion(notion_token)))
    else:
        checks.append(("Notion", missing_env_error("NOTION_TOKEN")))
    if github_token and repo:
        checks.append(("Etiquetas", lambda: check_labels(repo, github_token)))
    return checks


def render_report(results):
    lines = ["## Diagnóstico de automatizaciones", ""]
    ok_count = 0
    for name, ok, detail in results:
        icon = ":white_check_mark:" if ok else ":x:"
        if ok:
            ok_count += 1
        lines.append(f"- {icon} **{name}**: {detail}")
    lines.append("")
    lines.append(f"Resultado: {ok_count}/{len(results)} comprobaciones correctas")
    return "\n".join(lines)


def main():
    results = []
    for name, check in collect_checks():
        try:
            results.append((name, True, check()))
        except Exception as error:
            results.append((name, False, str(error)))
            print(f"aviso: {name} falló: {error}")

    report = render_report(results)
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
