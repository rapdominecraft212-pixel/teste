"""
Teste isolado para determinar se o problema esta na API ou no codigo.

Testa cada modelo com um prompt de texto SIMPLES (sem video)
e cada chave de API individualmente, com delays longos entre
requisicoes para evitar rate limit.

Uso:
    python test_api_standalone.py
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__) or ".")

import requests

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

MODEL_LIST = [
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
]

KEYS_FILE = os.path.join(os.path.dirname(__file__), "api_keys.json")


def load_keys():
    with open(KEYS_FILE, encoding="utf-8") as f:
        return json.load(f)["keys"]


PAYLOAD = {
    "contents": [{"parts": [{"text": "Responda apenas: OK. Nao escreva mais nada."}]}]
}


def test_model_com_chave(model: str, key: str) -> tuple:
    """
    Retorna (True, "") se funcionou ou (False, mensagem_de_erro).
    """
    url = f"{BASE_URL}/models/{model}:generateContent"
    headers = {"x-goog-api-key": key}
    try:
        resp = requests.post(url, headers=headers, json=PAYLOAD, timeout=60)
        if resp.status_code == 200:
            try:
                texto = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                if texto.strip():
                    return (True, texto)
                return (False, "resposta vazia")
            except (KeyError, IndexError, TypeError) as e:
                return (False, f"resposta inesperada: {e}")
        elif resp.status_code == 429:
            return (False, "429 RATE LIMIT")
        elif resp.status_code == 503:
            return (False, "503 OVERLOADED")
        elif resp.status_code == 401:
            return (False, "401 UNAUTHORIZED")
        elif resp.status_code == 403:
            return (False, "403 FORBIDDEN")
        else:
            return (False, f"HTTP {resp.status_code}: {resp.text[:100]}")
    except requests.exceptions.Timeout:
        return (False, "TIMEOUT (60s)")
    except requests.exceptions.ConnectionError:
        return (False, "CONNECTION ERROR")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


def main():
    keys = load_keys()
    print(f"{'='*65}")
    print(f"  TESTE ISOLADO: {len(keys)} chaves x {len(MODEL_LIST)} modelos")
    print(f"{'='*65}")
    print()

    resultados_por_modelo = {m: [] for m in MODEL_LIST}
    resumo_chaves = {f"chave #{i+1}": [] for i in range(len(keys))}

    for ki, key in enumerate(keys):
        print(f"{'='*65}")
        print(f"  CHAVE #{ki+1}: {key[:20]}...{key[-8:]}")
        print(f"{'='*65}")
        for mi, model in enumerate(MODEL_LIST):
            print(f"    Modelo {mi+1}/{len(MODEL_LIST)}: {model} ... ", end="", flush=True)
            ok, msg = test_model_com_chave(model, key)
            resultados_por_modelo[model].append((ki, ok, msg))
            resumo_chaves[f"chave #{ki+1}"].append((model, ok, msg))
            if ok:
                print(f"[OK] resposta: \"{msg}\"")
            else:
                print(f"[FALHA] {msg}")
            print()
            if mi < len(MODEL_LIST) - 1:
                print("      (aguardando 10s para nao queimar rate limit...)", flush=True)
                time.sleep(10)

    print()
    print(f"{'='*65}")
    print("  RESUMO POR MODELO")
    print(f"{'='*65}")
    for model in MODEL_LIST:
        sucessos = sum(1 for _, ok, _ in resultados_por_modelo[model] if ok)
        total = len(resultados_por_modelo[model])
        erros = [msg for _, ok, msg in resultados_por_modelo[model] if not ok]
        status = "[OK]" if sucessos == total else f"[FALHA] {sucessos}/{total} ok"
        print(f"  {model:30s} {status}")
        if erros:
            for e in set(erros):
                print(f"    -> {e}")

    print()
    print(f"{'='*65}")
    print("  RESUMO POR CHAVE")
    print(f"{'='*65}")
    for nome_chave, resultados in resumo_chaves.items():
        sucessos = sum(1 for _, ok, _ in resultados if ok)
        total = len(resultados)
        status = "[OK]" if sucessos == total else f"[FALHA] {sucessos}/{total} ok"
        erros = [msg for _, ok, msg in resultados if not ok]
        print(f"  {nome_chave:15s} {status}")
        if erros:
            for e in set(erros):
                print(f"    -> {e}")

    print()
    total_tests = len(keys) * len(MODEL_LIST)
    total_ok = sum(
        1 for model in MODEL_LIST
        for _, ok, _ in resultados_por_modelo[model]
        if ok
    )
    print(f"{'='*65}")
    print(f"  TOTAL: {total_ok}/{total_tests} testes OK")
    print(f"{'='*65}")

    if total_ok == 0:
        print()
        print("  >>> NENHUM teste passou. O problema e NAS CHAVES ou nos MODELOS.")
        print("  >>> Verifique se as chaves sao validas e tem faturamento ativo.")
        sys.exit(1)
    elif total_ok < total_tests:
        print()
        print("  >>> Alguns testes falharam. Reveja os erros acima.")
        sys.exit(1)
    else:
        print()
        print("  >>> TODOS OS TESTES PASSARAM. O problema esta no CODIGO do programa.")
        sys.exit(0)


if __name__ == "__main__":
    main()
