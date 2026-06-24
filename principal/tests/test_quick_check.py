"""Quick check: api_keys.json has 10 valid keys, index at 0"""
import json, requests
from pathlib import Path

keys_file = Path(__file__).resolve().parent.parent / "api_keys.json"
index_file = Path(__file__).resolve().parent.parent / "api_key_index.txt"

keys = json.loads(keys_file.read_text(encoding="utf-8"))["keys"]
idx = int(index_file.read_text().strip())

print(f"Chaves: {len(keys)}, Index: {idx}")
print(f"Primeira chave: {keys[0][:20]}...")

# Test first key
r = requests.post(
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
    headers={"x-goog-api-key": keys[0]},
    json={"contents": [{"parts": [{"text": "OK"}]}]},
    timeout=10,
)
print(f"Resultado: HTTP {r.status_code}")
if r.status_code != 200:
    err = r.json().get("error", {})
    print(f"  msg: {err.get('message', '')[:80]}")
