"""Send the daily summary to Telegram and/or WhatsApp (via the free CallMeBot service).
Each channel turns on automatically when its keys exist in .env."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("radar.alerts")


def _chunks(text: str, size: int) -> list[str]:
    parts, cur = [], ""
    for block in text.split("\n\n"):
        piece = block + "\n\n"
        if len(cur) + len(piece) > size and cur:
            parts.append(cur.strip())
            cur = ""
        while len(piece) > size:
            parts.append(piece[:size])
            piece = piece[size:]
        cur += piece
    if cur.strip():
        parts.append(cur.strip())
    return parts


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    ok = True
    for part in _chunks(text, 3800):
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat_id, "text": part, "disable_web_page_preview": True}, timeout=30)
            ok &= r.ok
            if not r.ok:
                log.warning("טלגרם החזיר שגיאה: %s", r.text[:200])
        except requests.RequestException as e:
            log.warning("שליחה לטלגרם נכשלה: %s", e)
            ok = False
    return ok


def send_whatsapp(phone: str, apikey: str, text: str) -> bool:
    ok = True
    for part in _chunks(text, 900):
        try:
            r = requests.get("https://api.callmebot.com/whatsapp.php",
                             params={"phone": phone, "text": part, "apikey": apikey}, timeout=30)
            ok &= r.ok
            if not r.ok:
                log.warning("וואטסאפ (CallMeBot) החזיר שגיאה: %s", r.text[:200])
        except requests.RequestException as e:
            log.warning("שליחה לוואטסאפ נכשלה: %s", e)
            ok = False
    return ok


def send_all(settings, text: str) -> list[str]:
    sent = []
    tok, chat = settings.secret("TELEGRAM_BOT_TOKEN"), settings.secret("TELEGRAM_CHAT_ID")
    if tok and chat and send_telegram(tok, chat, text):
        sent.append("טלגרם")
    phone, key = settings.secret("CALLMEBOT_PHONE"), settings.secret("CALLMEBOT_APIKEY")
    if phone and key and send_whatsapp(phone, key, text):
        sent.append("וואטסאפ")
    return sent
