import os
import sys
import requests
from bot import load_env_file, send_telegram_alert

def main():
    load_env_file()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "8950347796").strip()

    print("=" * 60)
    print("      TELEGRAM ALERT CONFIGURATION TEST")
    print("=" * 60)
    print(f"Chat ID: {chat_id}")
    if token:
        masked_token = token[:8] + "..." + token[-4:] if len(token) > 12 else "***"
        print(f"Bot Token: {masked_token}")
    else:
        print("Bot Token: [NOT SET] (Set in .env or TELEGRAM_BOT_TOKEN environment variable)")

    if not token:
        print("\n[NOTICE] To send live Telegram alerts:")
        print("1. Open Telegram and search for @BotFather.")
        print("2. Send /newbot, give it a name, and copy the HTTP API token.")
        print("3. Open the .env file in this directory and paste it:")
        print("   TELEGRAM_BOT_TOKEN=your_token_here")
        print("4. Re-run: python test_telegram.py")
        sys.exit(0)

    test_message = (
        "🤖 *PAPER TRADER ALERT SYSTEM ONLINE*\n\n"
        "✅ Telegram alert pipeline is active!\n"
        "• *Strategy:* 20 EMA Pullback (Nifty 200)\n"
        "• *Target/SL Alerts:* Enabled\n"
        "• *New Trade Alerts:* Enabled\n"
        "• *Daily EOD Summary:* 3:35 PM IST\n\n"
        "_Ready for automated execution._"
    )

    print("\nSending test notification...")
    success = send_telegram_alert(test_message)
    if success:
        print("SUCCESS! Test notification delivered to your Telegram.")
    else:
        print("FAILED to deliver message. Please verify your Bot Token and that you have started a chat with your bot on Telegram.")

if __name__ == "__main__":
    main()
