"""
webhook_app.py - Minimalist Webhook Endpoint for Telegram Status Listener
Designed for free 24/7 cloud hosting on Render.com (or Railway / Fly.io / Heroku).

Endpoints:
- GET  /              : Health check & quick webhook control panel.
- GET  /health        : Health check endpoint for cloud platform monitoring.
- POST /webhook       : Receives incoming Telegram updates and dispatches status replies.
- POST /              : Same as /webhook (for flexible Telegram configurations).
- GET  /set_webhook   : 1-click webhook registration with Telegram Bot API.
- GET  /webhook_info  : Inspects current Telegram webhook status.
- GET  /delete_webhook: Removes webhook registration (allows switching back to polling).

Run locally:
    uvicorn webhook_app:app --host 0.0.0.0 --port 8000 --reload

Run on Render:
    uvicorn webhook_app:app --host 0.0.0.0 --port $PORT
"""

import os
import sys
import json
import requests
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route

from bot import load_env_file
from database import init_db
import telegram_handler

load_env_file()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8950347796").strip()

# Initialize DB upon app startup
init_db()


async def health_check(request: Request) -> JSONResponse:
    """Cloud health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "paper-trader-telegram-webhook",
        "chat_id": TELEGRAM_CHAT_ID,
        "token_configured": bool(TELEGRAM_BOT_TOKEN),
        "timestamp_ist": telegram_handler.get_ist_now().isoformat()
    })


async def home_dashboard(request: Request) -> HTMLResponse:
    """Minimal visual control panel for verifying deployment and setting webhook."""
    host = request.headers.get("host", "localhost:8000")
    proto = request.headers.get("x-forwarded-proto", "https" if "onrender.com" in host else "http")
    webhook_url = f"{proto}://{host}/webhook"

    token_status = "✅ Configured" if TELEGRAM_BOT_TOKEN else "❌ Missing"
    chat_status = f"✅ Authorized: {TELEGRAM_CHAT_ID}" if TELEGRAM_CHAT_ID else "❌ Not set"

    summary = telegram_handler.get_portfolio_summary()
    equity_str = telegram_handler.format_inr(summary["total_equity"])
    cash_str = telegram_handler.format_inr(summary["cash"])

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Paper Trader Telegram Webhook</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0e1117;
            color: #f0f2f6;
            margin: 0;
            padding: 30px;
        }}
        .card {{
            background: #1e222d;
            border: 1px solid #2e3444;
            border-radius: 12px;
            padding: 24px;
            max-width: 650px;
            margin: 0 auto;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        }}
        h1 {{ margin-top: 0; font-size: 22px; color: #4caf50; display: flex; align-items: center; gap: 8px; }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            background: #1b3d2f;
            color: #4caf50;
        }}
        .info-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #2e3444;
            font-size: 14px;
        }}
        .info-label {{ color: #9aa0a6; }}
        .info-value {{ font-weight: 600; }}
        .btn-group {{ margin-top: 20px; display: flex; gap: 10px; flex-wrap: wrap; }}
        .btn {{
            display: inline-block;
            padding: 8px 16px;
            background: #238636;
            color: #fff;
            text-decoration: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
        }}
        .btn-secondary {{ background: #30363d; color: #c9d1d9; }}
        .btn-danger {{ background: #da3633; color: #fff; }}
        .btn:hover {{ opacity: 0.9; }}
        code {{
            background: #161b22;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 13px;
            color: #58a6ff;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🤖 Paper Trader Webhook Listener <span class="badge">ONLINE</span></h1>
        <p style="color: #9aa0a6; font-size: 14px;">24/7 Interactive Telegram Status Webhook Server</p>
        
        <div class="info-row">
            <span class="info-label">Bot Token:</span>
            <span class="info-value">{token_status}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Telegram Chat ID:</span>
            <span class="info-value">{chat_status}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Current Portfolio Equity:</span>
            <span class="info-value" style="color: #4caf50;">{equity_str}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Available Cash:</span>
            <span class="info-value">{cash_str}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Active Slots:</span>
            <span class="info-value">{summary['open_count']} / 5</span>
        </div>
        <div class="info-row">
            <span class="info-label">Suggested Webhook URL:</span>
            <span class="info-value"><code>{webhook_url}</code></span>
        </div>

        <div class="btn-group">
            <a class="btn" href="/set_webhook?url={webhook_url}" target="_blank">🔗 1-Click Register Webhook</a>
            <a class="btn btn-secondary" href="/webhook_info" target="_blank">ℹ️ Webhook Info</a>
            <a class="btn btn-secondary" href="/health" target="_blank">🩺 Health JSON</a>
            <a class="btn btn-danger" href="/delete_webhook" target="_blank">🗑️ Delete Webhook</a>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(html_content)


async def webhook_endpoint(request: Request) -> JSONResponse:
    """Handle incoming POST updates from Telegram."""
    try:
        data = await request.json()
    except Exception as e:
        print(f"[WARN] Failed to parse incoming JSON: {e}")
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    try:
        # Process the incoming message asynchronously
        handled = telegram_handler.process_update(data)
        return JSONResponse({"ok": True, "handled": handled})
    except Exception as e:
        print(f"[ERROR] Error handling Telegram update: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def set_webhook(request: Request) -> JSONResponse:
    """Register webhook URL with Telegram API."""
    if not TELEGRAM_BOT_TOKEN:
        return JSONResponse({"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}, status_code=400)

    target_url = request.query_params.get("url")
    if not target_url:
        host = request.headers.get("host", "localhost:8000")
        proto = request.headers.get("x-forwarded-proto", "https" if "onrender.com" in host else "http")
        target_url = f"{proto}://{host}/webhook"

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    try:
        resp = requests.post(api_url, json={"url": target_url, "drop_pending_updates": False}, timeout=15)
        return JSONResponse(resp.json())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def webhook_info(request: Request) -> JSONResponse:
    """Inspect current Telegram webhook status."""
    if not TELEGRAM_BOT_TOKEN:
        return JSONResponse({"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}, status_code=400)

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
    try:
        resp = requests.get(api_url, timeout=15)
        return JSONResponse(resp.json())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def delete_webhook(request: Request) -> JSONResponse:
    """Delete webhook from Telegram so polling mode can be used."""
    if not TELEGRAM_BOT_TOKEN:
        return JSONResponse({"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}, status_code=400)

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    try:
        resp = requests.post(api_url, json={"drop_pending_updates": False}, timeout=15)
        return JSONResponse(resp.json())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


routes = [
    Route("/", endpoint=home_dashboard, methods=["GET"]),
    Route("/", endpoint=webhook_endpoint, methods=["POST"]),
    Route("/webhook", endpoint=webhook_endpoint, methods=["POST"]),
    Route("/health", endpoint=health_check, methods=["GET"]),
    Route("/set_webhook", endpoint=set_webhook, methods=["GET"]),
    Route("/webhook_info", endpoint=webhook_info, methods=["GET"]),
    Route("/delete_webhook", endpoint=delete_webhook, methods=["GET"]),
]

app = Starlette(debug=False, routes=routes)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting Webhook App on 0.0.0.0:{port}...")
    uvicorn.run("webhook_app:app", host="0.0.0.0", port=port, reload=False)
