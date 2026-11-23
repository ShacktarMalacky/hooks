# hooks

Automatizaciones ligeras basadas en GitHub Actions.

## Slack → GitHub issues

Cada 15 minutos, [.github/workflows/slack-to-issues.yml](.github/workflows/slack-to-issues.yml)
lee los mensajes nuevos de un canal de Slack (`#general`) y crea un issue por
mensaje en este repositorio, etiquetado con `slack`.

### Cómo funciona

- El script [`scripts/slack_to_issues.py`](scripts/slack_to_issues.py) consulta
  `conversations.history` desde el último checkpoint (`.slack-state/checkpoint.json`).
- Solo crea issues para mensajes de personas (ignora bots, edits, joins e hilos).
- Al crear un issue, reacciona con ✅ al mensaje y responde en el hilo con el enlace.
- **Ciclo bidireccional**: las respuestas del hilo de Slack se copian como comentarios
  del issue, y los comentarios nuevos del issue se envían al hilo de Slack. Los
  comentarios del bot se filtran en ambas direcciones para evitar bucles.
- **Comandos desde Slack**: un mensaje con `cierra #12` cierra ese issue y `reabre #12`
  lo reabre (también valen `close`/`reopen`), confirmando el resultado en el hilo.
- Deduplica tres veces: ventana del checkpoint, marcador oculto
  `[slack-ts:<ts>]` buscado contra los issues existentes, y el checkpoint solo
  se avanza cuando los mensajes ya se procesaron.
- El checkpoint se comete únicamente cuando hubo mensajes nuevos, así el
  historial del repo no se llena de commits vacíos.
- Si `KEYWORDS` está definido (separado por comas), solo los mensajes que
  contengan todas las palabras crean issues.

### Puesta en marcha (una vez)

1. Crea una Slack App en https://api.slack.com/apps → *From scratch*.
2. En **OAuth & Permissions**, añade los *bot token scopes* `channels:history`,
   `chat:write` y `reactions:write` (y `users:read` para mostrar nombres de
   autor; sin él se usa el ID).
3. Instala la app en el workspace e invítala al canal: escribe
   `/invite @<tu-app>` en `#general`.
4. Guarda el token como secret del repo (empieza por `xoxb-…`):

   ```bash
   gh secret set SLACK_BOT_TOKEN --repo ShacktarMalacky/hooks
   ```

5. Ejecuta el workflow manualmente para probar: pestaña *Actions* →
   *Slack to GitHub issues* → *Run workflow*.

Mientras el secret no exista, el workflow termina sin error ni efecto.

### Variables de configuración (en el workflow)

| Variable | Valor actual |
|---|---|
| `SLACK_CHANNEL_ID` | `C0AV06CL03G` (`#general`) |
| `SLACK_CHANNEL_NAME` | `general` |
| `ISSUE_LABELS` | `slack` |
| `KEYWORDS` | vacío (todos los mensajes) |
| `THREADS_FILE` | `.slack-state/threads.json` (mapa issue ↔ hilo) |

Si algún efecto secundario de Slack falla (por ejemplo, falta un scope), el
issue ya creado no se pierde: el script registra un aviso y continúa.

## Desarrollo

```bash
python -m unittest discover -s tests -v
```
