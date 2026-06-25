import os
import sys
import re
import time
import base64
import tempfile
import requests
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from moviepy import VideoFileClip

# ================== CONFIG ==================
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

from src.key_manager import get_api_key, rotate_api_key, next_key, ban_api_key


def _headers():
    return {"x-goog-api-key": get_api_key()}


ROWS = 80
UPLOAD_TIMEOUT = (30, 300)  # (connect, read) — 300s para uploads grandes
UPLOAD_MAX_RETRIES = 5          # retries especificos para upload
MAX_POLL_TIME = 300

MODEL_LIST = [
    "gemini-3.5-flash",       # GA  (deu 503 = sobrecarregado, mas VALIDO)
    "gemini-2.5-pro",         # GA
    "gemini-2.5-flash",       # GA
    "gemini-3.1-flash-lite",  # GA
    "gemini-2.5-flash-lite",  # GA
    "gemini-3.1-pro-preview",  # PREVIEW (limit: 0 no free tier)
    "gemini-3-flash-preview",  # PREVIEW (limit: 0 no free tier)
]

# Modelo atual — resetado no inicio de cada job para evitar vazamento de estado
MODEL_IDX = 0

def reset_model_index():
    """Reseta o indice do modelo para o inicio da lista.
    Deve ser chamado no inicio de cada job (analisar_video)."""
    global MODEL_IDX
    MODEL_IDX = 0
API_TIMEOUT = 5       # timeout para calls leves (nao usado atualmente)
VIDEO_TIMEOUT = 120   # timeout para generateContent com video


def _fail(orig_exc: BaseException, context: str):
    raise RuntimeError(f"[FAIL] {context}: {orig_exc}") from None


MAX_ATTEMPTS = 50


def generate_content(payload):
    """Gera conteudo via Gemini API com rotação proativa de chaves.
    
    Cada chamada bem-sucedida rotaciona para a próxima chave (next_key),
    distribuindo a carga entre as 10 chaves. Se der 429, marca a chave
    como em cooldown e tenta a próxima.
    """
    global MODEL_IDX
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        model = MODEL_LIST[MODEL_IDX]
        key = get_api_key()
        url = f"{BASE_URL}/models/{model}:generateContent"
        print(f"    [gemini] chave={key[:8]}... modelo={model} tentativa={attempts}")
        try:
            response = requests.post(url, headers={"x-goog-api-key": key}, json=payload, timeout=VIDEO_TIMEOUT)
            if response.status_code in (401, 403, 429):
                try:
                    body = response.json()
                    msg = body.get("error", {}).get("message", response.text[:300])
                except Exception:
                    msg = response.text[:300]
                msg_lower = msg.lower()
                # 1. Modelo sem cota gratuita — avanca modelo
                if "limit: 0" in msg:
                    print(f"    [gemini] modelo {model} sem cota, ciclando...")
                    MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
                    time.sleep(1)
                    continue
                # 2. Quota diaria esgotada na chave — ban longo (24h)
                if any(word in msg_lower for word in ["quota", "exceeded", "daily limit", "rate limit"]):
                    print(f"    [gemini] {response.status_code} — quota excedida na chave, banindo por 24h")
                    rotate_api_key()
                    ban_api_key(permanent=False, duration=86400)
                    MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
                    time.sleep(2)
                    continue
                # 3. Chave invalida/revogada — ban permanente
                if any(word in msg_lower for word in ["not found", "invalid", "api key", "api key not valid"]):
                    print(f"    [gemini] {response.status_code} — chave invalida, banindo permanentemente")
                    ban_api_key(permanent=True)
                    MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
                    time.sleep(2)
                    continue
                # 4. Retry-after explicito
                retry_match = re.search(r"Please retry in\s+([\d.]+)s?", msg)
                if retry_match:
                    retry_s = float(retry_match.group(1))
                    print(f"    [gemini] {response.status_code} — retry em {retry_s}s")
                    rotate_api_key()
                    MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
                    time.sleep(min(retry_s, 60))
                    continue
                # 5. Fallback generico
                print(f"    [gemini] {response.status_code} — ciclando modelo+chave (fallback)")
                rotate_api_key()
                MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
                time.sleep(2)
                continue
            if response.status_code in (502, 503, 504):
                print(f"    [gemini] {response.status_code} — servidor sobrecarga, ciclando modelo")
                MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
                next_key()  # trocar chave também (distribui melhor)
                time.sleep(3)
                continue
            if not response.ok:
                print(f"    [gemini] erro {response.status_code} — ciclando modelo+chave")
                MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
                next_key()
                time.sleep(3)
                continue
            # SUCESSO — rotacionar proativamente para a próxima chave
            next_key()
            return response.json()
        except requests.exceptions.Timeout:
            print(f"    [gemini] timeout — ciclando modelo+chave")
            MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
            next_key()
            time.sleep(3)
            continue
        except requests.exceptions.ConnectionError:
            print(f"    [gemini] connection error — ciclando modelo+chave")
            next_key()
            MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
            time.sleep(3)
            continue
        except requests.exceptions.RequestException as e:
            print(f"    [gemini] erro request — ciclando modelo+chave")
            MODEL_IDX = (MODEL_IDX + 1) % len(MODEL_LIST)
            next_key()
            time.sleep(3)
            continue
    raise RuntimeError(f"[FALHA] Todos os {MAX_ATTEMPTS} modelos+chaves esgotados")


def generate_content_parallel(payload, start_idx=0, step=3):
    """Versao thread-safe para chamadas paralelas.
    
    Diferente de generate_content(), usa um indice de modelo LOCAL
    (nao global) comecando em start_idx e avancando de step em step.
    Nao modifica MODEL_IDX global nem chama next_key() — cada thread
    gerencia seu proprio indice sem interferir nas outras.
    """
    attempts = 0
    model_idx = start_idx
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        model = MODEL_LIST[model_idx]
        key = get_api_key()
        url = f"{BASE_URL}/models/{model}:generateContent"
        print(f"    [gemini:par] chave={key[:8]}... modelo={model} tentativa={attempts}")
        try:
            response = requests.post(url, headers={"x-goog-api-key": key}, json=payload, timeout=VIDEO_TIMEOUT)
            if response.status_code in (401, 403, 429):
                try:
                    body = response.json()
                    msg = body.get("error", {}).get("message", response.text[:300])
                except Exception:
                    msg = response.text[:300]
                msg_lower = msg.lower()
                if "limit: 0" in msg:
                    print(f"    [gemini:par] modelo {model} sem cota, ciclando...")
                    model_idx = (model_idx + step) % len(MODEL_LIST)
                    time.sleep(1)
                    continue
                if any(word in msg_lower for word in ["quota", "exceeded", "daily limit", "rate limit"]):
                    print(f"    [gemini:par] {response.status_code} — quota excedida na chave, banindo por 24h")
                    rotate_api_key()
                    ban_api_key(permanent=False, duration=86400)
                    model_idx = (model_idx + step) % len(MODEL_LIST)
                    time.sleep(2)
                    continue
                if any(word in msg_lower for word in ["not found", "invalid", "api key", "api key not valid"]):
                    print(f"    [gemini:par] {response.status_code} — chave invalida, banindo permanentemente")
                    ban_api_key(permanent=True)
                    model_idx = (model_idx + step) % len(MODEL_LIST)
                    time.sleep(2)
                    continue
                retry_match = re.search(r"Please retry in\s+([\d.]+)s?", msg)
                if retry_match:
                    retry_s = float(retry_match.group(1))
                    print(f"    [gemini:par] {response.status_code} — retry em {retry_s}s")
                    rotate_api_key()
                    model_idx = (model_idx + step) % len(MODEL_LIST)
                    time.sleep(min(retry_s, 60))
                    continue
                print(f"    [gemini:par] {response.status_code} — ciclando modelo+chave (fallback)")
                rotate_api_key()
                model_idx = (model_idx + step) % len(MODEL_LIST)
                time.sleep(2)
                continue
            if response.status_code in (502, 503, 504):
                print(f"    [gemini:par] {response.status_code} — servidor sobrecarga, ciclando modelo")
                model_idx = (model_idx + step) % len(MODEL_LIST)
                rotate_api_key()
                time.sleep(3)
                continue
            if not response.ok:
                print(f"    [gemini:par] erro {response.status_code} — ciclando modelo+chave")
                model_idx = (model_idx + step) % len(MODEL_LIST)
                rotate_api_key()
                time.sleep(3)
                continue
            # SUCESSO — nao rotaciona chave globalmente (thread-safe)
            return response.json()
        except requests.exceptions.Timeout:
            print(f"    [gemini:par] timeout — ciclando modelo+chave")
            model_idx = (model_idx + step) % len(MODEL_LIST)
            rotate_api_key()
            time.sleep(3)
            continue
        except requests.exceptions.ConnectionError:
            print(f"    [gemini:par] connection error — ciclando modelo+chave")
            rotate_api_key()
            model_idx = (model_idx + step) % len(MODEL_LIST)
            time.sleep(3)
            continue
        except requests.exceptions.RequestException as e:
            print(f"    [gemini:par] erro request — ciclando modelo+chave")
            model_idx = (model_idx + step) % len(MODEL_LIST)
            rotate_api_key()
            time.sleep(3)
            continue
    raise RuntimeError(f"[FALHA] Todos os {MAX_ATTEMPTS} modelos+chaves esgotados")


def extrair_texto(response):
    try:
        return response["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        _fail(e, f"extrair_texto: resposta inesperada do Gemini")


def limpar_code_block(texto: str) -> str:
    match = re.search(r"```\s*\n?(.*?)(?:\n?```|$)", texto, re.DOTALL)
    return match.group(1).strip() if match else texto.strip()


# ================== UPLOAD VÍDEO ==================

def _mime_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
            "avi": "video/x-msvideo", "mkv": "video/x-matroska"}.get(ext.lstrip("."), "video/mp4")


def upload_video(video_path):
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")
    file_size = os.path.getsize(video_path)
    file_size_mb = file_size / (1024 * 1024)
    mime = _mime_type(video_path)
    upload_url = "https://generativelanguage.googleapis.com/upload/v1beta/files?uploadType=multipart"
    metadata = {"file": {"display_name": os.path.basename(video_path)}}

    # Timeout adaptativo: mais tempo para arquivos maiores
    # 300s base + 60s por MB acima de 5MB
    adaptive_read_timeout = 300 + max(0, int((file_size_mb - 5) * 60))
    upload_timeout = (30, min(adaptive_read_timeout, 900))  # max 15 min

    print(f"  [upload] {os.path.basename(video_path)}: {file_size_mb:.1f}MB, timeout={upload_timeout[1]}s")

    for attempt in range(UPLOAD_MAX_RETRIES):
        try:
            key = get_api_key()
            print(f"  [upload] usando chave {key[:8]}... (tentativa {attempt+1})")
            with open(video_path, "rb") as f:
                files = {
                    "metadata": ("metadata", str(metadata), "application/json"),
                    "file": (os.path.basename(video_path), f, mime),
                }
                response = requests.post(upload_url, headers={"x-goog-api-key": key}, files=files, timeout=upload_timeout)
            if response.status_code in (401, 403, 429):
                try:
                    body = response.json()
                    msg = body.get("error", {}).get("message", response.text[:300])
                except Exception:
                    msg = response.text[:300]
                msg_lower = msg.lower()
                if any(word in msg_lower for word in ["not found", "invalid", "api key"]):
                    print(f"  [upload] Chave invalida (tentativa {attempt+1}), banindo permanentemente")
                    ban_api_key(permanent=True)
                    time.sleep(2)
                    continue
                if any(word in msg_lower for word in ["quota", "exceeded", "daily", "rate limit"]):
                    print(f"  [upload] Quota excedida (tentativa {attempt+1}), banindo por 24h")
                    ban_api_key(permanent=False, duration=86400)
                    time.sleep(5)
                    continue
                print(f"  [upload] Rate limit/403 (tentativa {attempt+1}), rotacionando chave...")
                rotate_api_key()
                time.sleep(2)
                continue
            response.raise_for_status()
            file_uri = response.json()["file"]["uri"]
            print(f"  [upload] OK: {file_uri}")
            return file_uri
        except requests.exceptions.Timeout as e:
            print(f"  [upload] Timeout na tentativa {attempt+1}/{UPLOAD_MAX_RETRIES}: {e}")
            time.sleep(5)
            continue
        except requests.exceptions.ConnectionError as e:
            # Write timeout vem como ConnectionError('Connection aborted.', TimeoutError('The write operation timed out'))
            print(f"  [upload] ConnectionError na tentativa {attempt+1}/{UPLOAD_MAX_RETRIES}: {e}")
            if attempt < UPLOAD_MAX_RETRIES - 1:
                wait = 10 * (attempt + 1)  # backoff: 10s, 20s, 30s...
                print(f"  [upload] Retentando em {wait}s...")
                time.sleep(wait)
                continue
            _fail(e, f"upload_video({os.path.basename(video_path)})")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code >= 500:
                print(f"  [upload] Erro {e.response.status_code} na tentativa {attempt+1}, retentando...")
                time.sleep(10)
                continue
            _fail(e, f"upload_video({os.path.basename(video_path)})")
        except Exception as e:
            _fail(e, f"upload_video({os.path.basename(video_path)})")
    raise RuntimeError(f"[FALHA] Upload esgotou {UPLOAD_MAX_RETRIES} tentativas")


def _req_with_rotate(method, url, **kwargs):
    from src.key_manager import _load_keys
    max_retries = len(_load_keys()) * 2
    kwargs.setdefault("timeout", (30, 120))
    for attempt in range(max_retries):
        try:
            resp = method(url, headers=_headers(), **kwargs)
        except requests.exceptions.ConnectionError as e:
            print(f"  [_req] ConnectionError tentativa {attempt+1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except requests.exceptions.Timeout:
            print(f"  [_req] Timeout tentativa {attempt+1}")
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            raise
        if resp.status_code in (401, 403, 429):
            try:
                body = resp.json()
                msg = body.get("error", {}).get("message", resp.text[:300])
            except Exception:
                msg = resp.text[:300]
            retry_match = re.search(r"Please retry in\s+([\d.]+)s?", msg)
            if retry_match:
                retry_s = float(retry_match.group(1))
                time.sleep(min(retry_s, 120))
                continue
            time.sleep(5)
            rotate_api_key()
            continue
        return resp
    raise RuntimeError(f"[FALHA] requests esgotou {max_retries} tentativas")


def aguardar_processamento(file_uri):
    file_id = file_uri.split("/")[-1]
    start = time.time()
    try:
        while time.time() - start < MAX_POLL_TIME:
            resp = _req_with_rotate(requests.get, f"{BASE_URL}/files/{file_id}")
            resp.raise_for_status()
            data = resp.json()
            state = data.get("state", "PROCESSING")
            elapsed = int(time.time() - start)
            if state == "ACTIVE":
                return data
            if state == "FAILED":
                raise RuntimeError(f"Gemini reportou falha no processamento do video")
            time.sleep(5)
        raise TimeoutError(f"Processamento excedeu {MAX_POLL_TIME}s")
    except TimeoutError:
        raise
    except Exception as e:
        _fail(e, f"aguardar_processamento({file_id})")


# ================== GRID ==================

def _find_font(size=16):
    candidates = [
        "arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            pass
    return ImageFont.load_default()


def criar_grid_imagem(image_path, output_path):
    try:
        img = Image.open(image_path).convert("RGBA")
        W, H = img.size
        cell_h = H / ROWS

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        for r in range(ROWS + 1):
            y = int(r * cell_h)
            draw.line([(0, y), (W, y)], fill=(255, 50, 50, 180), width=2)

        font = _find_font(11)
        lw, lh = 20, 14

        for r in range(ROWS):
            label = str(r + 1)
            yc = int(r * cell_h + cell_h / 2)
            yb = yc - lh // 2
            draw.rectangle([0, yb, lw, yb + lh], fill=(0, 0, 0, 210))
            draw.text((3, yb + 1), label, font=font, fill=(255, 255, 255, 255))

        result = Image.alpha_composite(img, overlay).convert("RGB")
        result.save(output_path, quality=95)
        return W, H, cell_h
    except Exception as e:
        _fail(e, f"criar_grid_imagem({os.path.basename(image_path)})")


# ================== TAREFA A: CAPA (TÍTULO) ==================

def tarefa_titulo(file_uri, mime_type):
    try:
        payload = {
            "contents": [{
                "parts": [
                    {"fileData": {"mimeType": mime_type, "fileUri": file_uri}},
                    {"text": (
                        "Você é um agente de Inteligência Artificial integrado a um sistema automatizado "
                        "de postagem para TikTok e Kwai. Sua função é atuar como especialista em retenção "
                        "de atenção e marketing de vídeos curtos.\n"
                        "INPUT:\n"
                        "Você receberá em anexo um arquivo de vídeo.\n"
                        "SUA TAREFA:\n"
                        "Analise o conteúdo do vídeo e crie o texto ideal para a "
                        "\"Capa\" (o pop-up inicial ou thumbnail).\n"
                        "DIRETRIZES CONCEITUAIS DA CAPA:\n"
                        " * Função de Vitrine: A capa não é o título do vídeo. Não escreva um resumo "
                        "da história. Ela serve exclusivamente para fisgar o espectador e promover a "
                        "identificação visual instantânea de \"quem\" ou \"o que\" está no vídeo.\n"
                        " * Práticas de Redes Sociais: Para funcionar no TikTok e Kwai, o texto precisa "
                        "ser direto e criar alta curiosidade. Deve ser extremamente curto "
                        "(idealmente de 2 a 5 palavras). Use palavras fortes ou gatilhos que façam "
                         "o usuário parar de rolar o feed.\n"
                         " * Não use emojis, emoticons ou caracteres especiais.\n"
                         "REGRA CRÍTICA DE OUTPUT (FORMATO ESTRITO):\n"
                         "Esta é uma requisição direta de sistema. Qualquer palavra gerada fora do "
                         "padrão exigido quebrará o código da automação que irá ler a sua resposta.\n"
                         "Você está terminantemente proibida de fornecer saudações, explicações do seu "
                         "raciocínio, confirmações ou qualquer texto conversacional.\n"
                         "Sua resposta final deve conter UNICAMENTE um bloco de código. Substitua a "
                         "palavra CAPA no template abaixo pelo texto magnético que você criou.\n\n"
                         "Entregue EXATAMENTE este formato e nada mais:\n\n"
                         "```\nCAPA\n\n```"
                    )}
                ]
            }]
        }
        resp = generate_content(payload)
        texto = limpar_code_block(extrair_texto(resp))
        return texto
    except Exception as e:
        _fail(e, "tarefa_titulo")


# ================== TAREFA B: TÍTULO (GANCHO) ==================

def tarefa_subtitulo(file_uri, mime_type):
    try:
        payload = {
            "contents": [{
                "parts": [
                    {"fileData": {"mimeType": mime_type, "fileUri": file_uri}},
                    {"text": (
                        "Você é um agente de Inteligência Artificial integrado a um sistema automatizado "
                        "de postagem para TikTok e Kwai. Sua função é atuar como especialista em retenção "
                        "de atenção, copywriting e marketing de vídeos curtos.\n"
                        "INPUT:\n"
                        "Você receberá em anexo um arquivo de vídeo.\n"
                        "SUA TAREFA:\n"
                        "Analise o conteúdo do vídeo e crie o texto ideal para o "
                        "\"Título\" (o pop-up de gancho/hook que prende a atenção nos primeiros segundos).\n"
                        "DIRETRIZES CONCEITUAIS DO TÍTULO:\n"
                        " * Função de Narrativa e Conflito: O título não é a capa. Ele não serve apenas "
                        "para identificar \"quem\" está no vídeo. O título deve resumir o assunto exato, "
                        "a mensagem central, a \"fofoca\" ou o conflito da história que está sendo contada.\n"
                        " * Práticas de Redes Sociais: O título deve atuar como um gancho irresistível. "
                        "Ele precisa abrir um loop de curiosidade na mente do espectador. Use frases "
                        "provocativas, que gerem urgência, dúvida ou forte interesse emocional. Mantenha "
                         "o texto curto e impactante (fácil de ler rapidamente na tela).\n"
                         " * Não use emojis, emoticons ou caracteres especiais.\n"
                         "REGRA CRÍTICA DE OUTPUT (FORMATO ESTRITO):\n"
                         "Esta é uma requisição direta de sistema. Qualquer palavra gerada fora do "
                         "padrão exigido quebrará o código da automação que irá ler a sua resposta.\n"
                         "Você está terminantemente proibida de fornecer saudações, explicações do seu "
                         "raciocínio, confirmações ou qualquer texto conversacional.\n"
                         "Sua resposta final deve conter UNICAMENTE um bloco de código. Substitua a "
                         "palavra TÍTULO no template abaixo pelo texto magnético que você criou.\n\n"
                         "Entregue EXATAMENTE este formato e nada mais:\n\n"
                         "```\nTÍTULO\n\n```"
                    )}
                ]
            }]
        }
        resp = generate_content(payload)
        texto = limpar_code_block(extrair_texto(resp))
        return texto
    except Exception as e:
        _fail(e, "tarefa_subtitulo")


# ================== TAREFA C: POSIÇÃO DE CORTE ==================

def tarefa_corte(grid_path, cell_h):
    try:
        with open(grid_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "contents": [{
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": img_b64,
                        }
                    },
                    {
                        "text": (
                            "Analise a imagem anexada e determine a faixa exata pertencente ao filme, "
                            "excluindo toda a área de edição.\n\n"
                            "Seja preciso: inclua o máximo de conteúdo possível, "
                            "excluindo apenas as fileiras que contêm artefatos visuais de edição.\n\n"
                            "Critério:\n"
                            "- Qualquer linha/frame que faça parte da fronteira entre edição e filme "
                            "deve ser classificada como edição.\n"
                            "- A linha/frame inicial do filme é o primeiro ponto completamente fora da edição.\n"
                            "- A linha/frame final do filme é o último ponto completamente fora da edição.\n\n"
                            "Retorne apenas:\n\n"
                            "Linha_inicial = [linha]\n"
                            "Linha_final = [linha]"
                        )
                    }
                ]
            }]
        }
        resp = generate_content(payload)
        texto = extrair_texto(resp)

        match = re.search(r"Linha_inicial\s*=\s*(\d+)[\s\S]*?Linha_final\s*=\s*(\d+)", texto.strip())
        if not match:
            raise ValueError(f"Nao foi possivel interpretar a resposta do Gemini: {texto}")

        row_start = int(match.group(1))
        row_end = int(match.group(2))
        y_start = int((row_start - 1) * cell_h)
        y_end = int(row_end * cell_h)

        return y_start, y_end
    except Exception as e:
        _fail(e, "tarefa_corte")


# ================== MAIN ==================

def analisar_video(video_path):
    try:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")

        # Resetar modelo no inicio de cada job (evita vazamento de estado entre jobs)
        reset_model_index()

        # 1. Upload
        file_uri = upload_video(video_path)

        # 2. Aguardar processamento
        file_data = aguardar_processamento(file_uri)
        mime_type = file_data.get("mimeType", "video/mp4")

        # 3. Frame + Grid
        with VideoFileClip(video_path) as clip:
            mid_frame = clip.duration / 2
            frame_np = clip.get_frame(mid_frame)
            frame = Image.fromarray(frame_np)

        # Usar arquivos temporarios UNICOS por job (evita colisao entre jobs concorrentes)
        tmp_dir = Path(tempfile.gettempdir())
        job_uid = os.path.basename(video_path).replace(".", "_") + f"_{id(video_path)}"
        frame_path = str(tmp_dir / f"frame_analise_{job_uid}.jpg")
        grid_path = str(tmp_dir / f"frame_grid_{job_uid}.jpg")
        frame.save(frame_path, quality=95)
        _, _, cell_h = criar_grid_imagem(frame_path, grid_path)

        # 4. Executar 3 tarefas SEQUENCIALMENTE
        # Cada tarefa usa uma chave DIFERENTE (rotação proativa)
        # Isso distribui a carga: 10 chaves × 10 RPM = 100 RPM

        resultados = {}

        resultados["titulo"] = tarefa_titulo(file_uri, mime_type)

        resultados["subtitulo"] = tarefa_subtitulo(file_uri, mime_type)

        resultados["corte"] = tarefa_corte(grid_path, cell_h)

        # Limpeza
        for p in [frame_path, grid_path]:
            try:
                os.remove(p)
            except Exception:
                pass

        return {
            "titulo": resultados["titulo"],
            "subtitulo": resultados["subtitulo"],
            "corte_y_start": resultados["corte"][0],
            "corte_y_end": resultados["corte"][1],
        }
    except Exception as e:
        _fail(e, f"analisar_video({os.path.basename(video_path)})")


# ================== PONTO DE ENTRADA ==================

if __name__ == "__main__":
    upload_dir = Path(__file__).parent / "upload"
    videos = sorted(upload_dir.glob("*.mp4"))
    if not videos:
        print("Nenhum video encontrado em upload/")
        sys.exit(1)

    video = str(videos[0])
    print(f"Analisando: {video}")
    resultado = analisar_video(video)

    print("\n" + "=" * 50)
    print("RESULTADOS FINAIS")
    print("=" * 50)
    print(f"Capa:\n{resultado['titulo']}")
    print(f"\nTitulo (gancho):\n{resultado['subtitulo']}")
    print(f"\nCorte Y: {resultado['corte_y_start']} a {resultado['corte_y_end']}")
