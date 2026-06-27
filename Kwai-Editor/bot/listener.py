import os
import sys
import time
import secrets
import threading
import requests
from pathlib import Path
from dotenv import load_dotenv
from db import (
    init_db, get_user_state, set_user_state, create_job, create_pending_job,
    flush_pending_jobs, count_pending, discard_pending_jobs,
    count_ready, count_processing, count_failed,
    count_by_status, retry_failed_jobs, get_user_jobs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
from checar_link import validar as validar_link

from log_utils import log
from telegram_utils import send_telegram_message, escape_html

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")

if not TOKEN:
    print("AVISO: TELEGRAM_BOT_TOKEN nao configurado. Bot listener nao pode iniciar.")
    print("Configure o arquivo .env com seu token do Telegram.")

session = requests.Session()
session.headers.update({"Connection": "keep-alive"})

# Rastrear threads de validação por chat — quando o usuário clica "Concluído",
# esperamos as threads terminarem antes de fazer flush_pending_jobs.
_validation_threads = {}  # chat_id -> [thread, ...]
_validation_lock = threading.Lock()

MENU_KEYBOARD = {
    "keyboard": [
        [{"text": "\U0001f4e4 Enviar link"}],
        [{"text": "\U0001f4e5 Meus v\u00eddeos"}]
    ],
    "resize_keyboard": True,
    "is_persistent": True
}

COLLECT_KEYBOARD = {
    "keyboard": [
        [{"text": "\u2705 Conclu\u00eddo"}],
        [{"text": "\u274c Cancelar"}]
    ],
    "resize_keyboard": True,
    "is_persistent": True
}


def api_url(method):
    return f"{BASE_URL}/bot{TOKEN}/{method}"


def show_main_menu(chat_id, welcome=False):
    if welcome:
        send_telegram_message(
            chat_id,
            "\U0001f3ac <b>Kwai Editor</b>\n\n"
            "Eu baixo v\u00eddeos do Kwai e edito automaticamente.\n"
            "Os v\u00eddeos prontos ficam no servidor.\n\n"
            "\U0001f4e4 <b>Enviar link</b> \u2014 Enviar um link do Kwai para edi\u00e7\u00e3o\n"
            "\U0001f4e5 <b>Meus v\u00eddeos</b> \u2014 Ver status dos v\u00eddeos",
            parse_mode="HTML",
            reply_markup=MENU_KEYBOARD
        )
    else:
        send_telegram_message(
            chat_id,
            "\U0001f3ac O que voc\u00ea quer fazer?",
            reply_markup=MENU_KEYBOARD
        )


def handle_start(chat_id):
    set_user_state(chat_id, "idle")
    show_main_menu(chat_id, welcome=True)


def handle_send_link(chat_id):
    set_user_state(chat_id, "collecting_links")
    send_telegram_message(
        chat_id,
        "\U0001f4e4 <b>Modo de coleta ativado!</b>\n\n"
        "Cole o link do Kwai que voc\u00ea quer editar.\n"
        "Voc\u00ea pode enviar v\u00e1rios links, um por mensagem.\n\n"
        "Quando terminar, toque em \u2705 <b>Conclu\u00eddo</b>.",
        parse_mode="HTML",
        reply_markup=COLLECT_KEYBOARD
    )


def handle_collecting_link(chat_id, text):
    raw = text.strip()
    lower = raw.lower().strip("/")

    if text == "\u2705 Conclu\u00eddo" or lower == "sair":
        set_user_state(chat_id, "idle")

        # Esperar threads de validação terminarem antes de fazer flush.
        # Isso garante que todos os links enviados tenham seus jobs criados
        # como 'pending' antes de transitá-los para 'queued'.
        with _validation_lock:
            threads = _validation_threads.pop(chat_id, [])
        for t in threads:
            t.join(timeout=30)  # Max 30s esperando validação

        # GATE: libera os jobs 'pending' deste chat para o worker processar.
        # Antes desta chamada o worker nao via os jobs (status='pending');
        # apos esta chamada eles viram 'queued' e o worker os pega em <1s.
        flush_pending_jobs(chat_id)

        ready = count_ready(chat_id)
        processing = count_processing(chat_id)
        failed = count_failed(chat_id)
        total = ready + processing + failed

        if total > 0:
            parts = []
            if processing > 0:
                parts.append(f"{processing} na fila")
            if ready > 0:
                parts.append(f"{ready} pronto(s)")
            if failed > 0:
                parts.append(f"{failed} com erro")
            detail = ", ".join(parts)

            send_telegram_message(
                chat_id,
                "\u2705 <b>Tudo certo!</b>\n\n"
                f"Tenho {escape_html(str(total))} v\u00eddeo(s): {escape_html(detail)}.\n"
                "Quando quiser ver o resultado, toque em \U0001f4e5 <b>Meus v\u00eddeos</b>.",
                parse_mode="HTML"
            )
        else:
            send_telegram_message(
                chat_id,
                "\u2705 <b>Tudo certo!</b>\n\n"
                "Nenhum link foi adicionado.",
                parse_mode="HTML"
            )
        show_main_menu(chat_id)
        return

    if text == "\u274c Cancelar":
        set_user_state(chat_id, "idle")
        # Descartar jobs 'pending' criados durante a coleta cancelada.
        # Jobs ja 'queued'/'processing'/'ready' (de coletas anteriores
        # confirmadas) NAO sao tocados.
        discarded = discard_pending_jobs(chat_id)
        if discarded > 0:
            msg = (
                "\u274c <b>Coleta cancelada.</b>\n\n"
                f"{escape_html(str(discarded))} link(s) descartado(s)."
            )
        else:
            msg = "\u274c <b>Coleta cancelada.</b>\n\nNenhum link foi salvo."
        send_telegram_message(
            chat_id,
            msg,
            parse_mode="HTML"
        )
        show_main_menu(chat_id)
        return

    # Validar link em background — NÃO bloquear o listener!
    # A validação (yt-dlp probe) pode levar 5-30s, e durante esse tempo
    # o listener não processaria outras mensagens. Rodando em thread,
    # o listener fica livre para responder outros usuários.
    def _validate_and_create():
        try:
            resultado = validar_link(raw)
        except RuntimeError as e:
            send_telegram_message(
                chat_id,
                f"\u26a0\ufe0f <b>Link inv\u00e1lido</b>\n\n{escape_html(e)}\n\n"
                "Formatos aceitos:\n"
                "  \u2022 https://k.kwai.com/p/...\n"
                "  \u2022 https://www.kwai.com/video/...",
                parse_mode="HTML"
            )
            return

        job_id = secrets.token_urlsafe(16)
        # IMPORTANTE: criar como 'pending' (nao 'queued') — o worker NAO ve jobs
        # pending. O job so vira 'queued' (e portanto visivel ao worker) quando o
        # usuario clicar em 'Concluido', via flush_pending_jobs(chat_id).
        create_pending_job(job_id, chat_id, resultado["clean_url"])
        # Sem mensagem de confirmacao por link — silencio = sucesso. A contagem
        # aparece apenas UMA vez, no resumo apos clicar em 'Concluido'. Mensagens
        # de erro (link invalido) continuam aparecendo normalmente.

    t = threading.Thread(target=_validate_and_create, daemon=True)
    t.start()
    # Rastrear thread para esperar quando usuário clicar "Concluído"
    with _validation_lock:
        if chat_id not in _validation_threads:
            _validation_threads[chat_id] = []
        _validation_threads[chat_id].append(t)


def handle_check_videos(chat_id):
    ready = count_ready(chat_id)
    processing = count_processing(chat_id)
    failed = count_failed(chat_id)

    if ready == 0 and processing == 0 and failed == 0:
        send_telegram_message(
            chat_id,
            "\U0001f4ed <b>Nenhum v\u00eddeo encontrado.</b>\n\n"
            "Toque em \U0001f4e4 <b>Enviar link</b> para come\u00e7ar.",
            parse_mode="HTML",
            reply_markup=MENU_KEYBOARD
        )
        return

    editado_dir = PROJECT_ROOT / "data" / "editado" / str(chat_id)

    parts = []
    if ready > 0:
        parts.append(f"\u2705 Pronto(s): {ready}")
    if processing > 0:
        parts.append(f"\U0001f4e4 Editando: {processing}")
    if failed > 0:
        parts.append(f"\u26a0\ufe0f Com erro: {failed}")

    status = "\n".join(parts)

    if ready > 0:
        arquivos = []
        if editado_dir.exists():
            arquivos = sorted(editado_dir.glob("*.mp4"))
        file_list = ""
        if arquivos:
            for f in arquivos:
                size = f.stat().st_size / (1024 * 1024)
                file_list += f"\n  \u2022 <code>{escape_html(f.name)}</code> ({size:.1f}MB)"
        else:
            file_list = "\n  (nenhum arquivo .mp4 encontrado)"

        send_telegram_message(
            chat_id,
            f"\U0001f4e5 <b>Seus v\u00eddeos</b>\n\n{escape_html(status)}\n\n"
            f"V\u00eddeos no servidor:{file_list}",
            parse_mode="HTML",
            reply_markup=MENU_KEYBOARD
        )
    elif processing > 0:
        send_telegram_message(
            chat_id,
            f"\U0001f4e5 <b>Seus v\u00eddeos</b>\n\n{escape_html(status)}\n\n"
            "Nenhum v\u00eddeo pronto ainda.\n"
            "Voc\u00ea ser\u00e1 avisado quando cada um ficar pronto.",
            parse_mode="HTML",
            reply_markup=MENU_KEYBOARD
        )
    else:
        send_telegram_message(
            chat_id,
            f"\U0001f4e5 <b>Seus v\u00eddeos</b>\n\n{escape_html(status)}\n\n"
            "Nenhum v\u00eddeo pronto.",
            parse_mode="HTML",
            reply_markup=MENU_KEYBOARD
        )

    if failed > 0:
        from db import get_user_jobs
        errors = [j for j in get_user_jobs(chat_id) if j["status"] == "failed"]
        msg = f"\u26a0\ufe0f <b>{escape_html(str(failed))} v\u00eddeo(s) com erro</b>\n\n"
        for j in errors:
            err = j.get("error_message") or "motivo desconhecido"
            url = j.get("input_url", "")
            display_url = url if len(url) <= 60 else url[:57] + "..."
            if err.startswith("url_invalid:"):
                display_err = err[len("url_invalid: "):]
            elif "download_failed" in err:
                display_err = "Falha no download do video"
            elif "saved_path" in err:
                display_err = "Arquivo baixado nao encontrado"
            elif "processing_failed" in err:
                inner = err.replace("processing_failed: ", "")
                if "[FAIL]" in inner:
                    display_err = "Erro na edicao: " + inner.split("[FAIL]")[-1].strip()[:80]
                else:
                    display_err = "Erro no processamento: " + inner[:80]
            else:
                display_err = err[:100]
            msg += f"\u2022 <code>{escape_html(display_url)}</code>\n  <i>{escape_html(display_err)}</i>\n\n"
        msg += "Tente enviar o link novamente."
        send_telegram_message(chat_id, msg, parse_mode="HTML", reply_markup=MENU_KEYBOARD)


def handle_status(chat_id):
    state = get_user_state(chat_id)
    counts = count_by_status(chat_id)
    total = sum(counts.values())
    parts = [f"\U0001f4cb <b>Status do chat {chat_id}</b>\n"]
    parts.append(f"Estado: <code>{escape_html(state)}</code>\n")
    if total == 0:
        parts.append("Nenhum job encontrado.")
    else:
        parts.append(f"Total: {total} job(s)")
        for s in ("pending", "queued", "processing", "preparing", "ready_to_render", "rendering", "ready", "failed"):
            c = counts.get(s, 0)
            if c > 0:
                parts.append(f"  \u2022 {escape_html(s)}: {c}")

    # verificar se o worker esta vivo (launcher.pid)
    pid_path = PROJECT_ROOT / "launcher.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            import psutil
            alive = psutil.pid_exists(pid)
            parts.append(f"\nWorker: {'\u2705 ativo' if alive else '\u274c morto'} (PID {pid})")
        except Exception:
            parts.append("\nWorker: \u2753 nao foi possivel verificar")
    else:
        parts.append("\nWorker: \u274c PID file nao encontrado (servidores nao iniciados via launcher)")

    send_telegram_message(chat_id, "\n".join(parts), parse_mode="HTML")


def handle_reprocess(chat_id):
    affected = retry_failed_jobs(chat_id)
    if affected > 0:
        msg = (
            f"\U0001f504 <b>{escape_html(str(affected))} job(s) reenfileirados!</b>\n\n"
            "Os jobs que falharam serao processados novamente."
        )
    else:
        msg = "\u2705 Nenhum job com erro encontrado para reprocessar."
    send_telegram_message(chat_id, msg, parse_mode="HTML")
    show_main_menu(chat_id)


def process_update(update):
    message = update.get("message")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    if not chat_id:
        return

    if text.startswith("/start"):
        handle_start(chat_id)
        return

    if text.startswith("/status"):
        handle_status(chat_id)
        return

    if text.startswith("/reprocess"):
        handle_reprocess(chat_id)
        return

    state = get_user_state(chat_id)

    if text == "\U0001f4e4 Enviar link":
        handle_send_link(chat_id)

    elif text == "\U0001f4e5 Meus v\u00eddeos":
        if state == "collecting_links":
            set_user_state(chat_id, "idle")
        handle_check_videos(chat_id)

    elif state == "collecting_links":
        handle_collecting_link(chat_id, text)

    else:
        send_telegram_message(
            chat_id,
            "\u2139\ufe0f Use o menu abaixo para navegar.",
            reply_markup=MENU_KEYBOARD
        )


def main():
    import atexit
    atexit.register(session.close)

    init_db()
    offset = None
    log.info("Listener iniciado. Aguardando mensagens...")

    while True:
        try:
            params = {
                "timeout": 30,
                "allowed_updates": '["message"]'
            }
            if offset is not None:
                params["offset"] = offset

            response = session.get(api_url("getUpdates"), params=params, timeout=40)
            response.raise_for_status()
            data = response.json()

            for update in data.get("result", []):
                try:
                    process_update(update)
                except Exception as e:
                    log.error(f"Erro processando update {update['update_id']}: {e}")
                    import traceback
                    traceback.print_exc()
                offset = update["update_id"] + 1

        except KeyboardInterrupt:
            log.info("Listener encerrado.")
            break
        except Exception as exc:
            log.error(f"Erro no listener: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
