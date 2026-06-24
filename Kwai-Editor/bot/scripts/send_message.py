import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
CHAT_ID = "6703086158"

url = f"{BASE_URL}/bot{TOKEN}/sendMessage"

response = requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": "Ol\u00e1! O bot est\u00e1 funcionando."
    },
    timeout=30
)

print(response.status_code)
print(response.text)
