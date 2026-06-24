"""
Teste da logica de MODEL_LIST cycling + KEY rotation.

Simula respostas HTTP para verificar que generate_content():
1. Comeca pelo modelo #1 com a chave atual
2. 429/401 -> rotaciona chave (5s), retry mesmo modelo
3. 503/timeout -> avanca modelo, mesma chave
4. Volta para o #1 quando chega no ultimo modelo
5. Nunca levanta excecao
"""
import sys, os, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch, Mock
from src import gemini_analyzer as ga


def reset_idx():
    ga.MODEL_IDX = 0


FAKE_KEY = "fake-key-abc"


def mock_get_key():
    return FAKE_KEY


def mock_rotate():
    return FAKE_KEY


def make_response(status=200, body=None):
    mock = Mock()
    mock.status_code = status
    mock.text = body or json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    mock.ok = status < 400
    try:
        mock.json.return_value = json.loads(mock.text)
    except json.JSONDecodeError:
        mock.json.side_effect = ValueError("invalid json")
    return mock


def test_success_starts_at_model_0():
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    with patch("requests.post", return_value=make_response(200)):
        result = ga.generate_content(payload)
        assert result["candidates"][0]["content"]["parts"][0]["text"] == "ok"
    print("[OK] test_success_starts_at_model_0")


def test_429_rotaciona_chave_avanca_modelo():
    """429 agora avanca modelo+chave (evita banir todas as chaves no mesmo modelo)."""
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    side_effects = [
        make_response(429, "quota exceeded"),
        make_response(200),
    ]
    patches = [
        patch("requests.post", side_effect=side_effects),
        patch("time.sleep"),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("src.key_manager.rotate_api_key", side_effect=mock_rotate),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
        # MODEL_IDX avancou (modelo + chave juntos)
        assert ga.MODEL_IDX == 1, f"MODEL_IDX deveria ser 1, mas e {ga.MODEL_IDX}"
    finally:
        for p in patches:
            p.stop()
    print("[OK] test_429_rotaciona_chave_avanca_modelo: 429 -> rotate_key + avanca modelo")


def test_503_avanca_modelo():
    """503 avanca modelo imediatamente (com 5s delay)."""
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    side_effects = [make_response(503, "overloaded"), make_response(200)]
    patches = [
        patch("time.sleep"),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("requests.post", side_effect=side_effects),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
    finally:
        for p in patches:
            p.stop()

    assert ga.MODEL_IDX == 1, f"Esperava idx=1, mas e {ga.MODEL_IDX}"
    print("[OK] test_503_avanca_modelo: 503 -> avancou para idx=1")


def test_timeout_avanca_modelo():
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    import requests
    side_effects = [
        requests.exceptions.Timeout("timeout"),
        make_response(200),
    ]
    patches = [
        patch("time.sleep"),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("requests.post", side_effect=side_effects),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
    finally:
        for p in patches:
            p.stop()

    # mock_post nao esta mais disponivel, verificar via MODEL_IDX
    assert ga.MODEL_IDX == 1, f"Esperava idx=1, mas e {ga.MODEL_IDX}"
    print("[OK] test_timeout_avanca_modelo: timeout -> avancou para idx=1")


def test_connection_error_avanca():
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    import requests
    side_effects = [
        requests.exceptions.ConnectionError("no route to host"),
        make_response(200),
    ]
    patches = [
        patch("time.sleep"),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("requests.post", side_effect=side_effects),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
    finally:
        for p in patches:
            p.stop()

    assert ga.MODEL_IDX == 1, f"Esperava idx=1, mas e {ga.MODEL_IDX}"
    print("[OK] test_connection_error_avanca: connection error -> avancou para idx=1")


def test_loop_wraps_around():
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    # 7 modelos falham 1x cada, depois 200 no modelo #0
    failures = [make_response(503, "overload") for _ in range(7)]
    side_effects = failures + [make_response(200)]

    patches = [
        patch("time.sleep"),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("requests.post", side_effect=side_effects),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
    finally:
        for p in patches:
            p.stop()

    assert ga.MODEL_IDX == 0, f"Esperava idx=0 (wrapped), mas e {ga.MODEL_IDX}"
    print("[OK] test_loop_wraps_around: 7 modelos 503 -> voltou para idx=0")


def test_modelo_alterna_cada_chamada():
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    # 1a chamada: 503 no #0 -> avanca modelo, 200 no #1
    patches = [
        patch("time.sleep"),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("requests.post", side_effect=[make_response(503), make_response(200)]),
    ]
    for p in patches:
        p.start()
    try:
        ga.generate_content(payload)
    finally:
        for p in patches:
            p.stop()

    assert ga.MODEL_IDX == 1 % len(ga.MODEL_LIST), f"Esperava idx=1, mas foi {ga.MODEL_IDX}"

    # 2a chamada: comeca do #1
    with patch("time.sleep"):
        with patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}):
            with patch("requests.post", return_value=make_response(200)) as mock_post:
                ga.generate_content(payload)

    called_url = mock_post.call_args[0][0]
    assert "gemini-2.5-pro" in called_url
    print(f"[OK] test_modelo_alterna_cada_chamada: chamada seguinte comecou em {called_url}")


def test_http_400_avanca():
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    side_effects = [make_response(400, "bad request"), make_response(200)]
    with patch("time.sleep"):
        with patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}):
            with patch("requests.post", side_effect=side_effects):
                result = ga.generate_content(payload)
                assert result is not None
    assert ga.MODEL_IDX == 1
    print("[OK] test_http_400_avanca: 400 pulou para idx=1")


def test_401_tambem_rotaciona_chave_avanca_modelo():
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    patches = [
        patch("time.sleep"),
        patch("requests.post", side_effect=[make_response(401, "unauthorized"), make_response(200)]),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("src.key_manager.rotate_api_key", side_effect=mock_rotate),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
        assert ga.MODEL_IDX == 1
    finally:
        for p in patches:
            p.stop()
    print("[OK] test_401_tambem_rotaciona_chave_avanca_modelo")


def test_chave_429_segue_apos_varias_rotacoes_avanca_modelo():
    """Multiplos 429 seguidos rotacionam chave E modelo."""
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    side_effects = [
        make_response(429, "quota"),
        make_response(429, "quota"),
        make_response(429, "quota"),
        make_response(200),
    ]
    patches = [
        patch("time.sleep"),
        patch("requests.post", side_effect=side_effects),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("src.key_manager.rotate_api_key", side_effect=mock_rotate),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
        # MODEL_IDX avancou 3x (um por erro)
        assert ga.MODEL_IDX == 3
    finally:
        for p in patches:
            p.stop()
    print("[OK] test_chave_429_segue_apos_varias_rotacoes_avanca_modelo")


# ================== TESTES 403 ==================

def test_403_quota_exceeded_rotaciona_chave_avanca_modelo():
    """403 com 'quota exceeded' -> rotaciona chave + avanca modelo."""
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    body_403 = json.dumps({"error": {"message": "Quota exceeded for this API key"}})
    side_effects = [
        make_response(403, body_403),
        make_response(200),
    ]
    mock_ban = Mock()
    patches = [
        patch("time.sleep"),
        patch("requests.post", side_effect=side_effects),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("src.key_manager.rotate_api_key", side_effect=mock_rotate),
        patch("src.gemini_analyzer.ban_api_key", mock_ban),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
        assert ga.MODEL_IDX == 1, "quota exceeded deve avancar modelo"
        mock_ban.assert_called_once_with(permanent=False, duration=86400)
    finally:
        for p in patches:
            p.stop()
    print("[OK] test_403_quota_exceeded_rotaciona_chave_avanca_modelo")


def test_403_limit_zero_cicla_modelo():
    """403 com 'limit: 0' -> avanca modelo (sem cota gratuita)."""
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    body_403 = json.dumps({"error": {"message": "limit: 0 for this model"}})
    side_effects = [
        make_response(403, body_403),
        make_response(200),
    ]
    patches = [
        patch("time.sleep"),
        patch("requests.post", side_effect=side_effects),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
        assert ga.MODEL_IDX == 1, "limit:0 deve avancar modelo"
    finally:
        for p in patches:
            p.stop()
    print("[OK] test_403_limit_zero_cicla_modelo")


def test_403_invalid_key_ban_permanente_avanca_modelo():
    """403 com 'API key not found' -> ban permanente na chave + avanca modelo."""
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    body_403 = json.dumps({"error": {"message": "API key not found. Please pass a valid API key."}})
    side_effects = [
        make_response(403, body_403),
        make_response(200),
    ]
    mock_ban = Mock()
    patches = [
        patch("time.sleep"),
        patch("requests.post", side_effect=side_effects),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("src.gemini_analyzer.ban_api_key", mock_ban),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
        assert ga.MODEL_IDX == 1, "chave invalida deve avancar modelo"
        mock_ban.assert_called_once_with(permanent=True)
    finally:
        for p in patches:
            p.stop()
    print("[OK] test_403_invalid_key_ban_permanente_avanca_modelo")


def test_403_generico_rotaciona_chave_avanca_modelo():
    """403 sem mensagem conhecida -> fallback: cicla modelo+chave."""
    reset_idx()
    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    body_403 = json.dumps({"error": {"message": "Some unknown 403 error"}})
    side_effects = [
        make_response(403, body_403),
        make_response(200),
    ]
    patches = [
        patch("time.sleep"),
        patch("requests.post", side_effect=side_effects),
        patch("src.gemini_analyzer._headers", return_value={"x-goog-api-key": FAKE_KEY}),
        patch("src.key_manager.rotate_api_key", side_effect=mock_rotate),
    ]
    for p in patches:
        p.start()
    try:
        result = ga.generate_content(payload)
        assert result is not None
        assert ga.MODEL_IDX == 1, "403 generico deve avancar modelo"
    finally:
        for p in patches:
            p.stop()
    print("[OK] test_403_generico_rotaciona_chave_avanca_modelo")


if __name__ == "__main__":
    tests = [
        test_success_starts_at_model_0,
        test_429_rotaciona_chave_avanca_modelo,
        test_503_avanca_modelo,
        test_timeout_avanca_modelo,
        test_connection_error_avanca,
        test_loop_wraps_around,
        test_modelo_alterna_cada_chamada,
        test_http_400_avanca,
        test_401_tambem_rotaciona_chave_avanca_modelo,
        test_chave_429_segue_apos_varias_rotacoes_avanca_modelo,
        test_403_quota_exceeded_rotaciona_chave_avanca_modelo,
        test_403_limit_zero_cicla_modelo,
        test_403_invalid_key_ban_permanente_avanca_modelo,
        test_403_generico_rotaciona_chave_avanca_modelo,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ERRO] {t.__name__}: {type(e).__name__}: {e}")

    print(f"\n{'='*40}")
    print(f"Resultado: {passed}/{len(tests)} testes passaram")
    sys.exit(0 if passed == len(tests) else 1)
