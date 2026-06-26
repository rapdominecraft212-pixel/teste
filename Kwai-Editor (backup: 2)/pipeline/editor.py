import sys
import os
import shutil
import time
import ctypes
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich.text import Text

from src.display import Display

from src import cortar_minuto, cortar_resolusao
from Playwright import qwen_capa, qwen_titulo
from src import colocar_linha
from src import renderizar

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "upload"
EDITADO_DIR = BASE_DIR / "data" / "editado"
BIBLIOTECA_DIR = BASE_DIR / "data" / "biblioteca"

TIMINGS_PADRAO = {
    "popup_1_in": 0.0,
    "popup_1_out": 7.0,
    "transition_dur": 1.0,
    "popup_fade_in": 1.5,
    "text_fade_dur": 0.5,
}


KWAI_ART = [
    "#  #   #     #     ###   #####",
    "# #    #     #   #   #    #  ",
    "##     #  #  #   #####    #  ",
    "# #    # # # #   #   #    #  ",
    "#  #   ##   ##   #   #   #####",
]


def center_terminal():
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            win_w = int(screen_w * 0.55)
            win_h = int(screen_h * 0.65)
            x = (screen_w - win_w) // 2
            y = (screen_h - win_h) // 2
            user32.SetWindowPos(hwnd, 0, x, y, win_w, win_h, 0x0040)
    except Exception:
        pass


def build_logo() -> Panel:
    logo = "\n".join(KWAI_ART) + "\n\n     editor"
    return Panel(
        Align.center(logo),
        border_style="orange3",
        padding=(1, 2),
    )


def show_splash(console: Console, video_count: int):
    console.clear()
    console.print()
    console.print(build_logo())
    console.print()

    msg = Text()
    msg.append("  [ ", style="white")
    msg.append("Pressione ENTER para iniciar o processamento", style="orange3")
    msg.append(" ]  ", style="white")

    console.print(Align.center(msg))
    console.print()
    console.print(
        Align.center(
            Text(f"Arquivos: {video_count} encontrados em upload/", style="white")
        )
    )
    console.print()

    console.input("")


def show_summary(console: Console, total: int, ok: int, fail: int):
    console.clear()
    console.print()

    title = Text()
    title.append("PROCESSAMENTO CONCLUIDO", style="bold orange3")
    console.print(Align.center(title))
    console.print()

    lines = [
        f"  {total} videos processados",
        f"  [orange3]{ok} OK[/orange3]",
        f"  {fail} falhas" if fail else "  0 falhas",
    ]
    for line in lines:
        console.print(Align.center(line))

    console.print()
    console.print(Align.center("Saidas em:  editado/", style="white"))
    console.print(Align.center("Origens em:  biblioteca/", style="white"))
    console.print()

    if fail:
        console.print(
            Align.center(
                "Alguns videos falharam. Verifique upload/ para reprocessar.",
                style="orange3",
            )
        )
        console.print()

    msg = Text()
    msg.append("  [ ", style="white")
    msg.append("Pressione ENTER para fechar", style="orange3")
    msg.append(" ]  ", style="white")
    console.print(Align.center(msg))
    console.input("")


def processar_video(
    video_path: str,
    chat_id: int,
    display: Display,
    timings: dict,
) -> tuple:
    display.set_log("[1/7] Analisando...")

    # ========== ETAPA PARALELA ==========
    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_minuto = pool.submit(cortar_minuto.analisar, video_path, chat_id)
        fut_capa = pool.submit(qwen_capa.analisar, video_path)
        fut_titulo = pool.submit(qwen_titulo.analisar, video_path)
        fut_linha = pool.submit(colocar_linha.analisar, video_path)

        caminho_minutado = None
        texto_capa = None
        texto_titulo = None
        y1 = y2 = None

        for future in as_completed([fut_minuto, fut_capa, fut_titulo, fut_linha]):
            try:
                result = future.result()
            except Exception as e:
                for f in [fut_minuto, fut_capa, fut_titulo, fut_linha]:
                    f.cancel()
                raise RuntimeError(f"Falha em task paralela: {e}") from e

            if future == fut_minuto:
                caminho_minutado = result
                display.set_log("[2/7] Minutagem OK")
                display.advance()
            elif future == fut_capa:
                texto_capa = result
                display.set_log("[3/7] Capa OK")
                display.advance()
            elif future == fut_titulo:
                texto_titulo = result
                display.set_log("[4/7] Titulo OK")
                display.advance()
            elif future == fut_linha:
                y1, y2 = result
                display.set_log("[5/7] Linha OK")
                display.advance()

    # ========== ETAPA SEQUENCIAL ==========
    display.set_log("[6/7] Cortando resolucao...")
    caminho_resolvido = cortar_resolusao.analisar(caminho_minutado, y1, y2, chat_id)
    display.set_log("[6/7] Corte OK")
    display.advance()

    display.set_log("[7/7] Renderizando...")
    final_path = renderizar.analisar(
        caminho_resolvido,
        texto_capa,
        texto_titulo,
        chat_id=chat_id,
        on_render_progress=lambda pct: display.set_pct(pct),
        timings=timings,
    )
    display.set_log("[7/7] Render OK")
    display.advance()

    return texto_capa, texto_titulo, y1, y2, final_path


def main():
    center_terminal()

    for d in [UPLOAD_DIR, EDITADO_DIR, BIBLIOTECA_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    console = Console()

    videos = sorted(UPLOAD_DIR.glob("**/*.mp4"))
    if not videos:
        show_splash(console, 0)
        console.print("[orange3]Nenhum video encontrado em upload/[/orange3]")
        console.input("Pressione ENTER para sair...")
        return

    show_splash(console, len(videos))
    console.clear()

    ok_count = 0
    fail_count = 0
    start_time = time.monotonic()

    with Display(console, start_time) as display:
        for idx, video_path in enumerate(videos):
            # Extrair chat_id do caminho: data/upload/{chat_id}/video.mp4
            rel = video_path.relative_to(UPLOAD_DIR)
            chat_id = int(rel.parts[0]) if len(rel.parts) > 1 else 0

            display.set_file(idx + 1, len(videos))
            display.reset_tasks()
            display.set_log(video_path.name)

            try:
                titulo, subtitulo, y1, y2, final_path = processar_video(
                    str(video_path), chat_id, display, TIMINGS_PADRAO,
                )
                ok_count += 1
                display.set_log(f"[OK] {video_path.name}")

                # Arquivar original
                bib_dir = BIBLIOTECA_DIR / str(chat_id)
                bib_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(video_path), str(bib_dir / video_path.name))

            except Exception as e:
                fail_count += 1
                line = str(e).splitlines()[0]
                if "[FALHA]" in line or "[FAIL]" in line:
                    display_msg = line[:100]
                elif "[TIMEOUT]" in line:
                    display_msg = "Timeout"
                else:
                    display_msg = line[:100]
                display.set_log(f"[Falha] {video_path.name}: {display_msg}")

            time.sleep(0.5)

    show_summary(console, len(videos), ok_count, fail_count)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        console = Console()
        console.clear()
        console.print()
        console.print(Align.center(Text("ERRO INESPERADO", style="bold red")))
        console.print()
        console.print(Align.center(Text(str(e), style="white")))
        console.print()
        console.print(Align.center("Pressione ENTER para fechar..."))
        console.input()
