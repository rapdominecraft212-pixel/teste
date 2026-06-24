import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")

url = f"{BASE_URL}/bot{TOKEN}/getUpdates"
response = requests.get(url, timeout=30)
data = response.json()

print(json.dumps(data, indent=2, ensure_ascii=False))
