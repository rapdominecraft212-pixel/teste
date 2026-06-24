import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
_session = requests.Session()
_session.headers.update({"Connection": "keep-alive"})


def escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram_message(chat_id, text, parse_mode=None, reply_markup=None):
    data = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    url = f"{API}/bot{TOKEN}/sendMessage"

    for attempt in range(3):
        try:
            r = _session.post(url, json=data, timeout=30)
            r.raise_for_status()
            return True
        except Exception:
            if attempt == 2:
                return False
            time.sleep(2 ** attempt)

    return False
