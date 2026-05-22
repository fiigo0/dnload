#!/usr/bin/env python3
"""Generate Dnlod.icns from the procedural app icon."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image

from dnlod import make_app_icon

ROOT = Path(__file__).resolve().parent
ICONSET = ROOT / "build" / "Dnlod.iconset"
ICNS = ROOT / "Dnlod.icns"

SIZES = [16, 32, 64, 128, 256, 512, 1024]


def main() -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)

    base = make_app_icon().resize((1024, 1024), Image.LANCZOS)
    for size in SIZES:
        img = base.resize((size, size), Image.LANCZOS)
        img.save(ICONSET / f"icon_{size}x{size}.png")
        if size <= 512:
            img2 = base.resize((size * 2, size * 2), Image.LANCZOS)
            img2.save(ICONSET / f"icon_{size}x{size}@2x.png")

    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    print(f"Built {ICNS}")


if __name__ == "__main__":
    main()
