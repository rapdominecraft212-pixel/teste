"""Teste rapido: chave #1 funciona para upload mas nao para generate?"""
import json, requests

keys = json.load(open("api_keys.json"))["keys"]
key0 = keys[0]

print("=== UPLOAD com chave #1 ===")
metadata = '{"file": {"display_name": "test.mp4"}}'
r = requests.post(
    "https://generativelanguage.googleapis.com/upload/v1beta/files?uploadType=multipart",
    headers={"x-goog-api-key": key0},
    files={"metadata": ("metadata", metadata, "application/json")},
    timeout=15,
)
print(f"Status: {r.status_code}")
print(f"Body: {r.text[:200]}")
print()

print("=== GENERATE com chave #1 ===")
r2 = requests.post(
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
    headers={"x-goog-api-key": key0},
    json={"contents": [{"parts": [{"text": "OK"}]}]},
    timeout=10,
)
print(f"Status: {r2.status_code}")
print(f"Body: {r2.text[:200]}")
print()

print("=== GENERATE com chave #2 ===")
key1 = keys[1]
r3 = requests.post(
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
    headers={"x-goog-api-key": key1},
    json={"contents": [{"parts": [{"text": "OK"}]}]},
    timeout=10,
)
print(f"Status: {r3.status_code}")
print(f"Body: {r3.text[:200]}")
