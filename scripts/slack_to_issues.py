#!/usr/bin/env python3
"""Create GitHub issues from new Slack messages and keep both sides in sync.

Features:
- New top-level human messages in a Slack channel become GitHub issues.
- On creation, the bot reacts to the message and replies in the thread with
  the issue link.
- Thread replies become issue comments; issue comments become thread replies.
- Slack control commands `cierra #N` / `reabre #N` close or reopen issues.
- Bot comments are filtered out on both sides so the sync cannot loop.
"""

import difflib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from datetime import timedelta

SLACK_API = "https://slack.com/api"
GITHUB_API = "https://api.github.com"

MAX_TITLE_LENGTH = 70
REACTION_EMOJI = "white_check_mark"
DEFAULT_SIMILARITY_THRESHOLD = 0.8
DEFAULT_STALE_DAYS = 30
DEFAULT_STALE_CLOSE_DAYS = 7
DEFAULT_SPAM_MAX_PER_HOUR = 5
CLASSIFY_RULES = [
    ("prioridad:alta", r"urgente|critico|production|no funciona nada|lo antes posible|caida"),
    ("bug", r"\berror\b|excepcion|crash|\bfallo\b|\bbug\b|no funciona|se rompe|explota"),
    ("mejora", r"\bmejora\b|sugerencia|seria posible|podriamos|\bagregar\b|\banadir\b|\bseria bueno\b"),
    ("pregunta", r"\?|\bcómo\b|como hago|que pasa si|\bse puede\b"),
]
COMMAND_RE = re.compile(
    r"\b(cierra|cerrar|close|reabre|reabrir|reopen)\s*#\s*(\d+)\b|\b(cierra|cerrar|close|reabre|reabrir|reopen)\s+(\d+)\b",
    re.IGNORECASE,
)
CLOSE_WORDS = ("cierra", "cerrar", "close")


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


def slack_post(token, channel, text, thread_ts=None):
    body = {"channel": channel, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    result = http_json(f"{SLACK_API}/chat.postMessage", method="POST", token=token, body=body)
    if not result.get("ok"):
        raise RuntimeError(f"Slack API chat.postMessage failed: {result.get('error')}")
    return result


def slack_react(token, channel, ts, name):
    result = http_json(
        f"{SLACK_API}/reactions.add",
        method="POST",
        token=token,
        body={"channel": channel, "timestamp": ts, "name": name},
    )
    if not result.get("ok"):
        raise RuntimeError(f"Slack API reactions.add failed: {result.get('error')}")
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


def quote_text(text):
    return "\n".join(f"> {line}" for line in text.splitlines())


def resolve_author(token, user_id):
    if not user_id:
        return "desconocido"
    try:
        user = slack_get("users.info", token, user=user_id).get("user") or {}
        profile = user.get("profile") or {}
        return profile.get("display_name") or profile.get("real_name") or user_id
    except RuntimeError:
        return user_id


def normalize_text(text):
    decomposed = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def classify(text):
    normalized = normalize_text(text)
    labels = []
    for label, pattern in CLASSIFY_RULES:
        if re.search(pattern, normalized):
            labels.append(label)
    return labels


def fetch_open_issues(repo, token):
    return http_json(
        f"{GITHUB_API}/repos/{repo}/issues?state=open&per_page=100&sort=updated",
        token=token,
    )


def find_similar_issue(repo, token, text, threshold):
    candidate = normalize_text(text)
    try:
        open_issues = fetch_open_issues(repo, token)
    except Exception as error:
        print(f"  aviso: no se pudieron leer issues abiertos para comparar: {error}")
        return None
    best = None
    best_ratio = 0.0
    for issue in open_issues:
        if issue.get("pull_request"):
            continue
        ratio = difflib.SequenceMatcher(
            None, candidate, normalize_text(issue.get("title", ""))
        ).ratio()
        if ratio > best_ratio:
            best, best_ratio = issue, ratio
    return best if best_ratio >= threshold else None


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


def gh_add_comment(repo, token, number, body):
    return http_json(
        f"{GITHUB_API}/repos/{repo}/issues/{number}/comments",
        method="POST",
        token=token,
        body={"body": body},
    )


def gh_set_issue_state(repo, token, number, state):
    return http_json(
        f"{GITHUB_API}/repos/{repo}/issues/{number}",
        method="PATCH",
        token=token,
        body={"state": state},
    )


def ensure_labels(repo, token, labels):
    if not labels:
        return
    try:
        existing = {
            label["name"]
            for label in http_json(
                f"{GITHUB_API}/repos/{repo}/labels?per_page=100", token=token
            )
        }
    except Exception as error:
        print(f"  aviso: no se pudieron leer las etiquetas: {error}")
        return
    for label in labels:
        if label in existing:
            continue
        try:
            http_json(
                f"{GITHUB_API}/repos/{repo}/labels",
                method="POST",
                token=token,
                body={"name": label},
            )
        except Exception as error:
            print(f"  aviso: no se pudo crear la etiqueta '{label}': {error}")


def hour_bucket(now=None):
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H")


def get_spam_counts(state, now=None):
    bucket = hour_bucket(now)
    spam = state.setdefault("spam", {})
    if spam.get("hour") != bucket:
        spam["hour"] = bucket
        spam["counts"] = {}
    return spam.setdefault("counts", {})


def spam_allows(counts, author_id, limit):
    return limit <= 0 or counts.get(author_id, 0) < limit


def find_warn_date(comments):
    dates = re.findall(r"\[stale:(\d{4}-\d{2}-\d{2})\]", "".join(
        comment.get("body", "") for comment in comments
    ))
    if not dates:
        return None
    return max(datetime.strptime(date, "%Y-%m-%d") for date in dates)


def manage_stale(repo, token, stale_days, close_days, now=None):
    now = now or datetime.now(timezone.utc)
    changed = False
    try:
        open_issues = fetch_open_issues(repo, token)
    except Exception as error:
        print(f"  aviso: no se pudieron revisar issues inactivos: {error}")
        return False
    for issue in open_issues:
        if issue.get("pull_request"):
            continue
        body = issue.get("body") or ""
        if "slack-ts:" not in body and "notion-row:" not in body:
            continue
        try:
            updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if now - updated < timedelta(days=stale_days):
            continue
        comments = http_json(
            f"{GITHUB_API}/repos/{repo}/issues/{issue['number']}/comments?per_page=100",
            token=token,
        )
        warn_date = find_warn_date(comments)
        if warn_date is None:
            today = now.strftime("%Y-%m-%d")
            gh_add_comment(
                repo,
                token,
                issue["number"],
                (
                    f":warning: Sin actividad desde hace {stale_days} dias. "
                    f"Se cerrara automaticamente en {close_days} dias si nadie "
                    f"responde. Responde para conservarlo. [stale:{today}]"
                ),
            )
            print(f"  issue #{issue['number']}: aviso de inactividad")
            changed = True
        elif now - warn_date.replace(tzinfo=timezone.utc) > timedelta(days=close_days):
            gh_set_issue_state(repo, token, issue["number"], "close")
            print(f"  issue #{issue['number']}: cerrado por inactividad")
            changed = True
    return changed


def parse_command(text):
    match = COMMAND_RE.search(text or "")
    if not match:
        return None
    word = match.group(1) or match.group(3)
    number = match.group(2) or match.group(4)
    action = "close" if word.lower() in CLOSE_WORDS else "open"
    return action, int(number)


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


def load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    state.setdefault("issues", {})
    state.setdefault("replies", {})
    state.setdefault("comments", {})
    return state


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def notify_issue_created(slack_token, channel_id, ts, issue):
    number = issue.get("number", "?")
    link = issue.get("html_url", "")
    title = issue.get("title", "")
    try:
        slack_react(slack_token, channel_id, ts, REACTION_EMOJI)
    except Exception as error:
        print(f"  aviso: no se pudo reaccionar al mensaje: {error}")
    try:
        slack_post(
            slack_token,
            channel_id,
            f":white_check_mark: Issue creado: #{number} — {title}\n{link}",
            thread_ts=ts,
        )
    except Exception as error:
        print(f"  aviso: no se pudo responder en el hilo: {error}")


def apply_command(action, number, author, repo, token, slack_token, channel_id, ts):
    state_label = "cerrado" if action == "close" else "reabierto"
    try:
        gh_set_issue_state(repo, token, number, action)
    except Exception as error:
        print(f"- {ts}: falló {state_label} #{number}: {error}")
        return False
    try:
        slack_post(
            slack_token,
            channel_id,
            f":white_check_mark: Issue #{number} {state_label} por {author}",
            thread_ts=ts,
        )
    except Exception as error:
        print(f"  aviso: no se pudo confirmar en Slack: {error}")
    return True


def sync_thread_replies(state, repo, github_token, slack_token, channel_id):
    changed = False
    for number, info in sorted(state["issues"].items(), key=lambda item: float(item[1]["ts"])):
        last = state["replies"].get(number, info["ts"])
        try:
            replies = slack_get(
                "conversations.replies",
                slack_token,
                channel=info.get("channel", channel_id),
                ts=info["ts"],
                oldest=last,
                limit=200,
            ).get("messages", [])
        except Exception as error:
            print(f"  aviso: no se pudo leer el hilo del issue #{number}: {error}")
            continue
        for reply in sorted(replies, key=lambda message: float(message.get("ts", "0"))):
            ts = reply.get("ts", last)
            if ts == info["ts"]:
                last = ts
                continue
            if is_issue_worthy(reply):
                author = resolve_author(slack_token, reply.get("user"))
                body = f"**{author} (Slack):**\n\n{quote_text(reply['text'])}"
                try:
                    gh_add_comment(repo, github_token, int(number), body)
                    print(f"  hilo -> issue #{number}: comentario de {author}")
                except Exception as error:
                    print(f"  aviso: no se pudo comentar en el issue #{number}: {error}")
            last = max(last, ts, key=float)
            changed = True
        if replies:
            state["replies"][number] = last
    return changed


def sync_issue_comments(state, repo, github_token, slack_token, channel_id):
    changed = False
    for number, info in sorted(state["issues"].items(), key=lambda item: float(item[1]["ts"])):
        last = state["comments"].get(number, 0)
        try:
            comments = http_json(
                f"{GITHUB_API}/repos/{repo}/issues/{number}/comments?sort=created&direction=asc&per_page=100",
                token=github_token,
            )
        except Exception as error:
            print(f"  aviso: no se pudieron leer los comentarios del issue #{number}: {error}")
            continue
        for comment in comments:
            if comment["id"] <= last:
                continue
            user = comment.get("user") or {}
            if user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]"):
                last = max(last, comment["id"])
                changed = True
                continue
            author = user.get("login", "desconocido")
            try:
                slack_post(
                    slack_token,
                    info.get("channel", channel_id),
                    f":speech_balloon: *{author}* comentó en #{number}:\n{quote_text(comment.get('body', ''))}",
                    thread_ts=info["ts"],
                )
                print(f"  issue #{number} -> hilo: comentario de {author}")
            except Exception as error:
                print(f"  aviso: no se pudo avisar al hilo del issue #{number}: {error}")
            last = max(last, comment["id"])
            changed = True
        if comments:
            state["comments"][number] = last
    return changed


def main():
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not slack_token:
        print("WARNING: SLACK_BOT_TOKEN no esta configurado; no se hace nada.")
        print("Configura el token en el repo y ejecuta el workflow manualmente para probar.")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY")
    github_token = os.environ["GITHUB_TOKEN"]
    channel_id = os.environ["SLACK_CHANNEL_ID"]
    channel_name = os.environ.get("SLACK_CHANNEL_NAME", "general")
    checkpoint_file = os.environ.get("CHECKPOINT_FILE", ".slack-state/checkpoint.json")
    state_file = os.environ.get("THREADS_FILE", ".slack-state/threads.json")
    labels = [label for label in os.environ.get("ISSUE_LABELS", "slack").split(",") if label]
    keywords = [kw.strip() for kw in os.environ.get("KEYWORDS", "").split(",") if kw.strip()]
    try:
        similarity_threshold = float(os.environ.get("DEDUP_THRESHOLD", DEFAULT_SIMILARITY_THRESHOLD))
    except ValueError:
        similarity_threshold = DEFAULT_SIMILARITY_THRESHOLD
    try:
        stale_days = int(os.environ.get("STALE_DAYS", DEFAULT_STALE_DAYS))
        stale_close_days = int(os.environ.get("STALE_CLOSE_DAYS", DEFAULT_STALE_CLOSE_DAYS))
    except ValueError:
        stale_days, stale_close_days = DEFAULT_STALE_DAYS, DEFAULT_STALE_CLOSE_DAYS
    try:
        spam_limit = int(os.environ.get("SPAM_MAX_PER_HOUR", DEFAULT_SPAM_MAX_PER_HOUR))
    except ValueError:
        spam_limit = DEFAULT_SPAM_MAX_PER_HOUR

    state = load_state(state_file)
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
    spam_counts = get_spam_counts(state)
    for message in messages:
        ts = message["ts"]
        text = message["text"]
        command = parse_command(text)
        if command:
            action, number = command
            author = resolve_author(slack_token, message.get("user"))
            apply_command(action, number, author, repo, github_token, slack_token, channel_id, ts)
        elif not matches_keywords(text, keywords):
            print(f"- {ts}: omitido por filtro de palabras clave")
        elif issue_exists(repo, github_token, ts):
            print(f"- {ts}: ya tiene un issue, se omite")
        else:
            author = resolve_author(slack_token, message.get("user"))
            similar = find_similar_issue(repo, github_token, make_title(text), similarity_threshold)
            if similar:
                print(
                    f"- {ts}: similar al issue #{similar['number']} "
                    f"({similar.get('title', '')!r}); se comenta en lugar de crear uno"
                )
                try:
                    gh_add_comment(
                        repo,
                        github_token,
                        similar["number"],
                        f"**{author} (Slack):**\n\n{quote_text(text)}\n\n"
                        f"Mensaje repetido desde Slack. [slack-ts:{ts}]",
                    )
                except Exception as error:
                    print(f"  aviso: no se pudo comentar el duplicado: {error}")
            else:
                author_id = message.get("user") or "desconocido"
                if not spam_allows(spam_counts, author_id, spam_limit):
                    print(f"- {ts}: límite de {spam_limit} issues/hora para {author_id}")
                    try:
                        slack_post(
                            slack_token,
                            channel_id,
                            f":no_entry: Has alcanzado el límite de {spam_limit} reportes "
                            f"por hora. Prueba de nuevo más tarde.",
                            thread_ts=ts,
                        )
                    except Exception as error:
                        print(f"  aviso: no se pudo avisar por Slack: {error}")
                    newest_ts = ts
                    continue
                spam_counts[author_id] = spam_counts.get(author_id, 0) + 1
                changed = True
                title = make_title(text)
                body = format_issue_body(message, author, channel_id, channel_name)
                issue_labels = labels + [label for label in classify(text) if label not in labels]
                ensure_labels(repo, github_token, issue_labels)
                issue = create_issue(repo, github_token, title, body, issue_labels)
                print(f"- {ts}: issue creado -> {issue.get('html_url')}")
                created += 1
                notify_issue_created(slack_token, channel_id, ts, issue)
                state["issues"][str(issue.get("number"))] = {"ts": ts, "channel": channel_id}
        newest_ts = ts

    changed = sync_thread_replies(state, repo, github_token, slack_token, channel_id)
    changed = sync_issue_comments(state, repo, github_token, slack_token, channel_id) or changed

    if stale_days > 0:
        changed = manage_stale(repo, github_token, stale_days, stale_close_days) or changed

    if newest_ts != oldest:
        save_checkpoint(checkpoint_file, newest_ts)
        print(f"Checkpoint actualizado a {newest_ts}")
    if created or changed:
        save_state(state_file, state)
    print(f"Issues creados: {created}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
