"""
telegram_poll.py - Standalone Local Long-Polling Runner for Telegram Status Listener

Runs a robust 24/7 or on-demand polling loop to listen for commands:
- "status", "/status", "portfolio", "/portfolio"
- "regime", "/regime"
- "help", "/help", "start", "/start"

Usage:
    python telegram_poll.py
"""

import os
import sys
import time
import requests

from bot import load_env_file
from database import init_db
import telegram_handler

# Force UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_env_file()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8950347796").strip()


def delete_webhook_if_active(token: str) -> None:
    """Ensure any existing webhook is removed so long polling can receive updates."""
    try:
        url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        resp = requests.post(url, json={"drop_pending_updates": False}, timeout=10)
        data = resp.json()
        if data.get("ok"):
            print("[INFO] Webhook cleared. Polling mode ready.")
    except Exception as e:
        print(f"[WARN] Failed to check/clear webhook: {e}")


def main():
    print("=" * 65)
    print("   🤖 PAPER TRADER - TELEGRAM STATUS LISTENER (POLLING MODE)")
    print("=" * 65)

    if not TELEGRAM_BOT_TOKEN:
        print("[FATAL] TELEGRAM_BOT_TOKEN is not set in .env or environment!")
        sys.exit(1)

    masked_token = (
        TELEGRAM_BOT_TOKEN[:8] + "..." + TELEGRAM_BOT_TOKEN[-4:]
        if len(TELEGRAM_BOT_TOKEN) > 12
        else "***"
    )
    print(f"• Bot Token:        {masked_token}")
    print(f"• Authorized Chat:  {TELEGRAM_CHAT_ID}")
    print("• Supported Cmds:   /status, status, /portfolio, /regime, /help")

    # Initialize DB
    init_db()

    # Clear webhook to allow getUpdates
    delete_webhook_if_active(TELEGRAM_BOT_TOKEN)

    print("\n[ACTIVE] Listening for Telegram commands... (Press Ctrl+C to stop)")
    print("-" * 65)

    offset = None
    poll_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset

        try:
            resp = requests.get(poll_url, params=params, timeout=35)
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("ok"):
                    print(f"[WARN] Telegram API error: {data.get('description')}")
                    time.sleep(3)
                    continue

                updates = data.get("result", [])
                for update in updates:
                    update_id = update["update_id"]
                    offset = update_id + 1

                    msg = update.get("message") or update.get("edited_message")
                    if msg:
                        sender = msg.get("from", {}).get("first_name", "User")
                        text = msg.get("text", "")
                        chat_id = str(msg.get("chat", {}).get("id", ""))
                        now_str = telegram_handler.get_ist_now().strftime("%H:%M:%S")

                        print(f"[{now_str}] Received from {sender} (Chat {chat_id}): '{text}'")

                        # Process message & dispatch reply
                        processed = telegram_handler.process_update(update)
                        if processed:
                            print(f"[{now_str}] -> Sent status response to chat {chat_id}")
            elif resp.status_code == 409:
                print("[WARN] Webhook conflict detected. Attempting to delete webhook...")
                delete_webhook_if_active(TELEGRAM_BOT_TOKEN)
                time.sleep(2)
            else:
                print(f"[WARN] HTTP {resp.status_code}: {resp.text}")
                time.sleep(3)

        except requests.exceptions.Timeout:
            # Normal long-polling timeout with no new messages, continue next poll
            continue
        except requests.exceptions.ConnectionError as ce:
            print(f"[WARN] Connection error: {ce}. Retrying in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n[INFO] Polling listener stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] Unexpected polling loop error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
