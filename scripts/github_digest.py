#!/usr/bin/env python3
"""Post a weekly digest of repository issues to a Slack channel.

Queries issues created and closed in the last 7 days via the GitHub search
API and posts a compact summary with the Slack bot token. All free: GitHub
Actions on a public repo are unlimited and Slack messages cost nothing.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

SLACK_API = "https://slack.com/api"
GITHUB_API = "https://api.github.com"

MAX_LISTED = 10


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


def search_issues(repo, token, query_suffix):
    query = urllib.parse.quote(f"repo:{repo} is:issue {query_suffix}")
    result = http_json(f"{GITHUB_API}/search/issues?q={query}&per_page=100", token=token)
    return result.get("items", []), result.get("total_count", 0)


def slack_post(token, channel, text):
    result = http_json(
        f"{SLACK_API}/chat.postMessage",
        method="POST",
        token=token,
        body={"channel": channel, "text": text},
    )
    if not result.get("ok"):
        raise RuntimeError(f"Slack API chat.postMessage failed: {result.get('error')}")
    return result


def build_digest(created, closed, total_open, window_label):
    lines = [f"*Resumen de issues* ({window_label})", ""]
    lines.append(f":inbox_tray: *Abiertos en el periodo:* {len(created)}")
    for issue in created[:MAX_LISTED]:
        lines.append(f"  • #{issue['number']} — {issue['title']}")
    if len(created) > MAX_LISTED:
        lines.append(f"  • … y {len(created) - MAX_LISTED} más")
    lines.append("")
    lines.append(f":outbox_tray: *Cerrados en el periodo:* {len(closed)}")
    for issue in closed[:MAX_LISTED]:
        lines.append(f"  • #{issue['number']} — {issue['title']}")
    if len(closed) > MAX_LISTED:
        lines.append(f"  • … y {len(closed) - MAX_LISTED} más")

    label_counts = Counter()
    for issue in created + closed:
        for label in issue.get("labels", []):
            label_counts[label["name"]] += 1
    if label_counts:
        top = ", ".join(
            f"{name} ({count})" for name, count in label_counts.most_common(5)
        )
        lines.append("")
        lines.append(f":label: *Etiquetas más activas:* {top}")
    lines.append("")
    lines.append(f":dart: *Abiertos ahora mismo:* {total_open}")
    return "\n".join(lines)


def main():
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not slack_token or not github_token:
        print("WARNING: faltan SLACK_BOT_TOKEN o GITHUB_TOKEN; no se hace nada.")
        return 0
    repo = os.environ["GITHUB_REPOSITORY"]
    channel_id = os.environ["SLACK_CHANNEL_ID"]
    window_days = int(os.environ.get("DIGEST_WINDOW_DAYS", "7") or 7)

    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    window_label = f"últimos {window_days} días"

    created, _ = search_issues(repo, github_token, f"is:open created:>={since}")
    closed, _ = search_issues(repo, github_token, f"closed:>={since}")
    _, total_open = search_issues(repo, github_token, "is:open")

    message = build_digest(created, closed, total_open, window_label)
    slack_post(slack_token, channel_id, message)
    print("Digest publicado en Slack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
