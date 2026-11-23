#!/usr/bin/env python3
"""Create GitHub issues from new Slack messages in a configured channel.

Reads new top-level human messages from a Slack channel, creates one GitHub
issue per message, and advances a checkpoint file so the next run only sees
messages posted afterwards. Deduplication is layered: the checkpoint plus a
hidden `slack-ts` marker searched against existing issues.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SLACK_API = "https://slack.com/api"
GITHUB_API = "https://api.github.com"

MAX_TITLE_LENGTH = 70


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


def slack_get(method, token, **params):
    query = urllib.parse.urlencode(params)
    result = http_json(f"{SLACK_API}/{method}?{query}", token=token)
    if not result.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {result.get('error')}")
    return result


def is_issue_worthy(message):
    if message.get("subtype") is not None:
        return False
    if message.get("bot_id") or message.get("app_id"):
        return False
    if message.get("type") != "message":
        return False
    return bool((message.get("text") or "").strip())


def matches_keywords(text, keywords):
    if not keywords:
        return True
    lowered = text.lower()
    return all(keyword.lower() in lowered for keyword in keywords)


def make_title(text):
    first_line = text.strip().splitlines()[0].strip()
    if len(first_line) > MAX_TITLE_LENGTH:
        first_line = first_line[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return first_line or "(mensaje sin texto)"


def format_issue_body(message, author, channel_id, channel_name):
    sent_at = datetime.fromtimestamp(
        float(message["ts"]), timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    link = f"https://slack.com/app_redirect?channel={channel_id}&message_ts={message['ts']}"
    quoted = "\n".join(f"> {line}" for line in message["text"].splitlines())
    return (
        f"**Autor:** {author}\n"
        f"**Canal:** #{channel_name}\n"
        f"**Enviado:** {sent_at}\n"
        f"[slack-ts:{message['ts']}]\n\n"
        f"{quoted}\n\n"
        f"[Ver en Slack]({link})"
    )


def resolve_author(token, user_id):
    if not user_id:
        return "desconocido"
    try:
        user = slack_get("users.info", token, user=user_id).get("user") or {}
        profile = user.get("profile") or {}
        return profile.get("display_name") or profile.get("real_name") or user_id
    except RuntimeError:
        return user_id


def issue_exists(repo, token, ts):
    query = urllib.parse.quote(f'repo:{repo} is:issue "slack-ts:{ts}"')
    result = http_json(f"{GITHUB_API}/search/issues?q={query}&per_page=1", token=token)
    return result.get("total_count", 0) > 0


def create_issue(repo, token, title, body, labels):
    return http_json(
        f"{GITHUB_API}/repos/{repo}/issues",
        method="POST",
        token=token,
        body={"title": title, "body": body, "labels": labels},
    )


def load_checkpoint(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("last_ts", "0")
    except (OSError, ValueError):
        return "0"


def save_checkpoint(path, ts):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"last_ts": ts}, handle, indent=2)
        handle.write("\n")


def main():
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not slack_token:
        print("WARNING: SLACK_BOT_TOKEN no esta configurado; no se hace nada.")
        print("Configura el secret y ejecuta el workflow manualmente para probar.")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY")
    github_token = os.environ["GITHUB_TOKEN"]
    channel_id = os.environ["SLACK_CHANNEL_ID"]
    channel_name = os.environ.get("SLACK_CHANNEL_NAME", "general")
    checkpoint_file = os.environ.get("CHECKPOINT_FILE", ".slack-state/checkpoint.json")
    labels = [label for label in os.environ.get("ISSUE_LABELS", "slack").split(",") if label]
    keywords = [kw.strip() for kw in os.environ.get("KEYWORDS", "").split(",") if kw.strip()]

    oldest = load_checkpoint(checkpoint_file)
    history = slack_get(
        "conversations.history",
        slack_token,
        channel=channel_id,
        oldest=oldest,
        inclusive=False,
        limit=200,
    )
    messages = [
        message for message in history.get("messages", []) if is_issue_worthy(message)
    ]
    messages.sort(key=lambda message: float(message["ts"]))
    print(f"Mensajes nuevos candidatos: {len(messages)}")

    created = 0
    newest_ts = oldest
    for message in messages:
        ts = message["ts"]
        text = message["text"]
        if not matches_keywords(text, keywords):
            print(f"- {ts}: omitido por filtro de palabras clave")
            continue
        if issue_exists(repo, github_token, ts):
            print(f"- {ts}: ya tiene un issue, se omite")
        else:
            author = resolve_author(slack_token, message.get("user"))
            title = make_title(text)
            body = format_issue_body(message, author, channel_id, channel_name)
            issue = create_issue(repo, github_token, title, body, labels)
            print(f"- {ts}: issue creado -> {issue.get('html_url')}")
            created += 1
        newest_ts = ts

    if newest_ts != oldest:
        save_checkpoint(checkpoint_file, newest_ts)
        print(f"Checkpoint actualizado a {newest_ts}")
    print(f"Issues creados: {created}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
