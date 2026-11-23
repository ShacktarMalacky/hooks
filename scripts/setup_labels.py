#!/usr/bin/env python3
"""Bootstrap the standard repository labels used by the automations.

Creates the labels that do not exist yet; existing labels are left untouched.
Run it with GITHUB_TOKEN and GITHUB_REPOSITORY set, locally or from Actions.
"""

import json
import os
import sys
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"

DEFAULT_LABELS = [
    {"name": "slack", "color": "4A154B", "description": "Creado desde un mensaje de Slack"},
    {"name": "notion", "color": "000000", "description": "Creado desde una fila de Notion"},
    {"name": "bug", "color": "d73a4a", "description": "Algo no funciona"},
    {"name": "mejora", "color": "a2eeef", "description": "Nueva función o mejora"},
    {"name": "pregunta", "color": "d876e3", "description": "Requiere una respuesta"},
    {"name": "prioridad:alta", "color": "b60205", "description": "Atención urgente"},
]


def http_json(url, method="GET", token=None, body=None):
    request = urllib.request.Request(url, data=None, method=method)
    if body is not None:
        request.data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def ensure_labels(repo, token, definitions=None):
    definitions = definitions or DEFAULT_LABELS
    existing = {
        label["name"] for label in http_json(
            f"{GITHUB_API}/repos/{repo}/labels?per_page=100", token=token
        )
    }
    created = []
    for definition in definitions:
        name = definition["name"]
        if name in existing:
            continue
        try:
            http_json(
                f"{GITHUB_API}/repos/{repo}/labels",
                method="POST",
                token=token,
                body={
                    "name": name,
                    "color": definition.get("color", "ededed"),
                    "description": definition.get("description", ""),
                },
            )
            created.append(name)
            print(f"etiqueta creada: {name}")
        except Exception as error:
            print(f"aviso: no se pudo crear '{name}': {error}")
    return created


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        print("WARNING: faltan GITHUB_TOKEN o GITHUB_REPOSITORY; no se hace nada.")
        return 0
    created = ensure_labels(repo, token)
    print(f"Listo. Etiquetas creadas: {len(created)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
