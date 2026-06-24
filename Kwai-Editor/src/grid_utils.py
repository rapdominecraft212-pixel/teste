import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROWS = 80


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
        raise RuntimeError(f"criar_grid_imagem({os.path.basename(image_path)}): {e}") from None
