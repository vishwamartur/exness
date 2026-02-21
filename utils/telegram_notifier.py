"""
Telegram Notifier — MT5 Trading Bot
=====================================
Sends real-time trade alerts and summaries to a Telegram chat.

Setup (one-time):
  1. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env
  2. To find your CHAT_ID: run  python -m utils.telegram_notifier
  3. Send /start to your bot (@vcrpttrade_bot), then re-run the script

Usage:
  from utils.telegram_notifier import TelegramNotifier, notify
  notify("✅ Trade executed: EURUSD BUY")
"""

import os
import sys
import json
import logging
import threading
import requests
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import settings

logger = logging.getLogger("TelegramNotifier")


class TelegramNotifier:
    BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str = None, chat_id: str = None):
        self.token   = token   or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        self.chat_id = chat_id or getattr(settings, 'TELEGRAM_CHAT_ID', '')
        self._enabled = bool(self.token and self.chat_id)
        if not self._enabled:
            logger.warning("[Telegram] Token or Chat ID missing — notifications disabled")

    # ─── Core send ───────────────────────────────────────────────────────────
    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message. Non-blocking (fires in background thread)."""
        if not self._enabled:
            return False
        threading.Thread(
            target=self._send_sync,
            args=(text, parse_mode),
            daemon=True,
        ).start()
        return True

    def _send_sync(self, text: str, parse_mode: str = "HTML"):
        try:
            url = self.BASE.format(token=self.token, method="sendMessage")
            resp = requests.post(url, json={
                "chat_id":    self.chat_id,
                "text":       text,
                "parse_mode": parse_mode,
            }, timeout=10)
            if not resp.ok:
                logger.warning(f"[Telegram] Send failed: {resp.text[:120]}")
        except Exception as e:
            logger.warning(f"[Telegram] Send error: {e}")

    # ─── Formatted helpers ────────────────────────────────────────────────────
    def trade_executed(self, symbol, direction, lot, price, sl, tp):
        emoji = "🟢" if direction == "BUY" else "🔴"
        sl_str = f"{sl:.5f}" if sl else "—"
        tp_str = f"{tp:.5f}" if tp else "—"
        text = (
            f"{emoji} <b>TRADE EXECUTED</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Symbol : <code>{symbol}</code>\n"
            f"Dir    : <b>{direction}</b>\n"
            f"Lots   : <code>{lot:.2f}</code>\n"
            f"Price  : <code>{price:.5f}</code>\n"
            f"SL     : <code>{sl_str}</code>\n"
            f"TP     : <code>{tp_str}</code>\n"
            f"Time   : {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        )
        self.send(text)

    def trade_closed(self, symbol, direction, profit, pips=None):
        emoji = "✅" if profit >= 0 else "❌"
        pl_str = f"+{profit:.2f}" if profit >= 0 else f"{profit:.2f}"
        pips_str = f" ({pips:+.1f} pips)" if pips is not None else ""
        text = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Symbol : <code>{symbol}</code>\n"
            f"Dir    : {direction}\n"
            f"P&amp;L  : <b>{pl_str} USD{pips_str}</b>"
        )
        self.send(text)

    def scan_candidates(self, candidates: list):
        if not candidates:
            return
        lines = [f"📡 <b>SCAN — {len(candidates)} signal(s)</b>\n━━━━━━━━━━━━━━━━"]
        for c in candidates[:5]:
            emoji = "🟢" if c.get('direction') == 'BUY' else "🔴"
            lines.append(
                f"{emoji} <code>{c['symbol']}</code>  {c['direction']}  "
                f"Score:{c.get('score','?')}  ML:{c.get('ml_prob',0):.2f}"
            )
        self.send("\n".join(lines))

    def daily_summary(self, balance, equity, day_pl, total_trades, win_trades):
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
        emoji = "📈" if day_pl >= 0 else "📉"
        text = (
            f"{emoji} <b>DAILY SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Balance  : <code>{balance:,.2f}</code>\n"
            f"Equity   : <code>{equity:,.2f}</code>\n"
            f"Day P&amp;L : <b>{'+'if day_pl>=0 else ''}{day_pl:.2f} USD</b>\n"
            f"Trades   : {total_trades}  Win: {win_trades} ({win_rate:.0f}%)"
        )
        self.send(text)

    def kill_switch(self, symbol, loss_usd):
        text = (
            f"🚨 <b>KILL SWITCH ACTIVATED</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Recent loss: <b>{loss_usd:.2f} USD</b>\n"
            f"Trading paused for this symbol."
        )
        self.send(text)

    def alert(self, message: str):
        """Generic alert."""
        self.send(f"⚠️ {message}")

    def info(self, message: str):
        """Generic info."""
        self.send(f"ℹ️ {message}")


# ─── Singleton ────────────────────────────────────────────────────────────────
_notifier: TelegramNotifier | None = None

def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier

def notify(text: str):
    """Convenience shortcut — send a plain text alert."""
    get_notifier().send(text)


# ─── Setup helper (run directly to get Chat ID) ───────────────────────────────
if __name__ == "__main__":
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or input("Paste your bot token: ").strip()
    print(f"\nSend any message to @vcrpttrade_bot on Telegram, then press Enter...")
    input()

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, timeout=10)
    data = resp.json()

    updates = data.get("result", [])
    if not updates:
        print("No messages found. Make sure you sent /start to the bot first, then try again.")
    else:
        for upd in updates[-5:]:
            msg = upd.get("message", {})
            chat = msg.get("chat", {})
            print(f"\nChat ID   : {chat.get('id')}")
            print(f"Username  : {chat.get('username', '—')}")
            print(f"Message   : {msg.get('text', '—')}")
        print(f"\n→ Add to .env:  TELEGRAM_CHAT_ID={updates[-1]['message']['chat']['id']}")
