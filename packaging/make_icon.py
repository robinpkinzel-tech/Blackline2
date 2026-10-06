"""Erzeugt das Programmsymbol (PNG, ICNS für macOS, ICO für Windows) nur mit Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "build"


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size * 0.22
    d.rounded_rectangle((size * 0.06, size * 0.06, size * 0.94, size * 0.94), radius=r, fill=(28, 28, 32, 255))
    # "Dokument" mit Textzeilen, zwei davon geschwärzt
    px0, py0, px1, py1 = size * 0.25, size * 0.17, size * 0.75, size * 0.83
    d.rounded_rectangle((px0, py0, px1, py1), radius=size * 0.04, fill=(245, 245, 245, 255))
    line_h = size * 0.055
    y = py0 + size * 0.09
    for i in range(7):
        x1 = px1 - size * (0.08 if i % 3 else 0.2)
        if i in (1, 4):
            d.rounded_rectangle((px0 + size * 0.07, y, x1, y + line_h), radius=line_h / 3, fill=(15, 15, 18, 255))
        else:
            d.rounded_rectangle((px0 + size * 0.07, y + line_h * 0.3, x1, y + line_h * 0.7), radius=line_h / 4,
                                fill=(170, 170, 175, 255))
        y += line_h * 1.55
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.icns", format="ICNS")
    img.save(OUT / "icon.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("Symbole erzeugt in", OUT)


if __name__ == "__main__":
    main()
