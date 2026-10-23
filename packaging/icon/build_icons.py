"""Makes every icon size C.A.P. ships from cap-icon-1024.png (rendered from icon.html).

Run after changing the icon:
    python packaging/icon/build_icons.py

Writes packaging/msix/Assets/*.png (the MSIX tile and Store images) and packaging/icon/cap.ico (CAP.exe and the
Windows installer).
"""

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
MASTER = Image.open(HERE / "cap-icon-1024.png").convert("RGBA")
ASSETS = HERE.parent / "msix" / "Assets"


def square(size):
    return MASTER.resize((size, size), Image.LANCZOS)


def on_canvas(width, height, icon_size):
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(square(icon_size), ((width - icon_size) // 2, (height - icon_size) // 2))
    return canvas


ASSETS.mkdir(parents=True, exist_ok=True)
outputs = {
    "StoreLogo.png": square(50),
    "Square44x44Logo.png": square(44),
    "Square150x150Logo.png": on_canvas(150, 150, 120),
    "Wide310x150Logo.png": on_canvas(310, 150, 120),
}
for name, image in outputs.items():
    image.save(ASSETS / name)
    print(f"{ASSETS / name}: {image.size[0]} x {image.size[1]}")

MASTER.save(HERE / "cap.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"{HERE / 'cap.ico'}: 16-256 px")
