import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")

url = f"{BASE_URL}/bot{TOKEN}/getMe"
response = requests.get(url, timeout=30)

print(response.status_code)
print(response.text)
