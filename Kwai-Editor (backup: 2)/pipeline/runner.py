import sys, os, shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
os.chdir(str(BASE_DIR / "data"))

sys.path.insert(0, str(BASE_DIR))

from src.gemini_analyzer import analisar_video
import src.cortar_video as cortar_video
import src.video_popup_linear as video_popup_linear

print("[1/3] Analisando video...", flush=True)
videos = sorted(Path("upload").glob("*.mp4"))
if not videos:
    bib = sorted(Path("biblioteca").glob("*.mp4"))
    if bib:
        shutil.copy2(str(bib[0]), str(Path("upload", bib[0].name)))
        videos = sorted(Path("upload").glob("*.mp4"))
video_path = str(videos[0])
resultado = analisar_video(video_path)
print(f'  Capa: {resultado["titulo"]}', flush=True)
print(f'  Titulo: {resultado["subtitulo"]}', flush=True)
print(f'  Corte Y: {resultado["corte_y_start"]} a {resultado["corte_y_end"]}', flush=True)

print("[2/3] Cortando video...", flush=True)
cortado = cortar_video.cortar_video(
    video_path, resultado["corte_y_start"], resultado["corte_y_end"],
    output_dir="cortado",
)
print(f"  Cortado: {cortado}", flush=True)

print("[3/3] Renderizando video final...", flush=True)
final = video_popup_linear.criar_video(
    input_path=cortado,
    output_dir="editado",
    titulo=resultado["titulo"],
    subtitulo=resultado["subtitulo"],
)
print(f"  Final: {final}", flush=True)

shutil.move(video_path, str(Path("biblioteca", Path(video_path).name)))
for f in Path("cortado").iterdir():
    try: os.remove(str(f))
    except: pass
print("[OK] Pipeline completo!", flush=True)
