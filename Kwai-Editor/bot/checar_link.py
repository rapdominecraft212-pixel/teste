import sys
import json
import subprocess
from pathlib import Path

PADRAO_K_KWAI_P = "https://k.kwai.com/p/"
PADRAO_KWAI_VIDEO = "https://www.kwai.com/video/"
TIMEOUT_PROBE = 120


def _detectar_yt_dlp():
    try:
        r = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    raise RuntimeError(
        "yt-dlp não está instalado.\n"
        "Instale com: pip install yt-dlp"
    )


def _executar(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_PROBE)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "comando falhou")
    return r


def _buscar_padrao(texto, padrao):
    inicio = texto.find(padrao)
    if inicio == -1:
        return None
    return inicio, inicio + len(padrao)


def _extrair_url(texto, inicio_padrao, fim_padrao):
    resto = texto[fim_padrao:]
    fim_url = resto.find(" ")
    id_video = resto[:fim_url].strip() if fim_url != -1 else resto.strip()
    if not id_video:
        raise RuntimeError(
            "Link inválido: não foi possível extrair o ID do vídeo após o padrão."
        )
    return texto[inicio_padrao:fim_padrao] + id_video


def _normalizar_url(url):
    if url.startswith(PADRAO_KWAI_VIDEO):
        video_id = url[len(PADRAO_KWAI_VIDEO):]
        return f"https://k.kwai.com/p/{video_id}"
    return url


def validar(texto):
    if not texto or not texto.strip():
        raise RuntimeError("Nenhum texto foi enviado.")

    match = _buscar_padrao(texto, PADRAO_K_KWAI_P)
    if match is None:
        match = _buscar_padrao(texto, PADRAO_KWAI_VIDEO)

    if match is None:
        raise RuntimeError(
            "Nenhum link no formato aceito foi encontrado.\n\n"
            "Formatos aceitos:\n"
            "  \u2022 https://k.kwai.com/p/...\n"
            "  \u2022 https://www.kwai.com/video/..."
        )

    url_candidata = _extrair_url(texto, match[0], match[1])
    url_normalizada = _normalizar_url(url_candidata)

    yt_dlp_cmd = _detectar_yt_dlp()

    cmd = yt_dlp_cmd + [
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
        "--no-playlist",
        url_normalizada,
    ]

    try:
        result = _executar(cmd)
    except RuntimeError:
        if url_normalizada != url_candidata:
            cmd_orig = yt_dlp_cmd + [
                "--skip-download",
                "--dump-single-json",
                "--no-warnings",
                "--no-playlist",
                url_candidata,
            ]
            try:
                result = _executar(cmd_orig)
                url_normalizada = url_candidata
            except RuntimeError:
                raise RuntimeError(
                    f"Vídeo não encontrado — O link {url_candidata} "
                    "parece não existir ou não está mais disponível."
                )
        else:
            raise RuntimeError(
                f"Vídeo não encontrado — O link {url_candidata} "
                "parece não existir ou não está mais disponível."
            )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("Erro ao interpretar resposta do yt-dlp.")

    return {
        "ok": True,
        "clean_url": url_normalizada,
        "video_id": payload.get("id", ""),
        "title": payload.get("title", ""),
        "duration": payload.get("duration", 0),
    }
