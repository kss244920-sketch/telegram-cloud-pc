# Telegram Cloud PC Bot

Shift your Ubuntu PC development & `agy` workflow into a remote Cloud PC controlled via Telegram chat and Telegram Mini App terminal.

## Architecture

- **Backend**: FastAPI + Uvicorn web server running on `$PORT`.
- **Bot Engine**: `python-telegram-bot` running concurrently with live polling.
- **Health Monitoring**: `GET /health` endpoint for UptimeRobot (pings every 5 minutes to prevent Render free instance spin-down).
- **Security**: Strict single-user authorization for `@OwAtom` (`ALLOWED_USERNAME` / `ALLOWED_USER_ID`). All unknown users are silently ignored.
- **Conversation Management**: Multi-session support with `/new` and `/conversations` inline switcher.

## Quick Start (Local)

```bash
pip install -r requirements.txt
python main.py
```

Test health:
```bash
curl http://localhost:8000/health
```

## Render Deployment

1. Connect GitHub repo `kss244920-sketch/telegram-cloud-pc` to Render.
2. Select **Web Service** (Docker or Python).
3. Set Environment Variables:
   - `BOT_TOKEN`: `8798239169:AAFbzjM4n7wYzwVNrLbiw-cw0irXIGLI8yI`
   - `ALLOWED_USERNAME`: `OwAtom`
4. Deploy!
