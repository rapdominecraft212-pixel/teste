import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
CHAT_ID = "6703086158"
FILE_PATH = Path("teste.mp4")

url = f"{BASE_URL}/bot{TOKEN}/sendDocument"

with FILE_PATH.open("rb") as file_obj:
    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "caption": "Arquivo de teste enviado pelo computador."
        },
        files={
            "document": (FILE_PATH.name, file_obj)
        },
        timeout=(10, 600)
    )

print(response.status_code)
print(response.text)
