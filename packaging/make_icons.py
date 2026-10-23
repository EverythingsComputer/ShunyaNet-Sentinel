#!/usr/bin/env python3
"""Generate the platform icons for the frozen build from assets/app_icon.png.

    python packaging/make_icons.py [--src assets/app_icon.png] [--outdir packaging/icons]

Writes:
    appicon.ico          Windows executable icon (multi-size)
    AppIcon.iconset/     macOS iconset (10 PNGs); run `iconutil -c icns` on a
                         Mac to get AppIcon.icns (this script does that when it
                         is running on macOS, and prints the command otherwise)

Pillow is a build-time dependency only — the app itself never imports it.
"""
import argparse
import os
import subprocess
import sys

SIZES_ICO = [256, 128, 64, 48, 32, 16]
# name suffix -> pixel size, per Apple's iconset convention
ICONSET = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="assets/app_icon.png")
    ap.add_argument("--outdir", default=os.path.join("packaging", "icons"))
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required to build icons:  pip install pillow", file=sys.stderr)
        return 2

    if not os.path.exists(args.src):
        print("source icon not found: %s" % args.src, file=sys.stderr)
        return 2

    os.makedirs(args.outdir, exist_ok=True)
    src = Image.open(args.src).convert("RGBA")
    # centre on a square canvas so odd aspect ratios never distort
    side = max(src.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(src, ((side - src.width) // 2, (side - src.height) // 2), src)

    ico_path = os.path.join(args.outdir, "appicon.ico")
    canvas.save(ico_path, format="ICO", sizes=[(s, s) for s in SIZES_ICO])
    print("wrote %s (%d bytes)" % (ico_path, os.path.getsize(ico_path)))

    set_dir = os.path.join(args.outdir, "AppIcon.iconset")
    os.makedirs(set_dir, exist_ok=True)
    resample = getattr(Image, "Resampling", Image).LANCZOS
    for name, px in ICONSET.items():
        canvas.resize((px, px), resample).save(os.path.join(set_dir, name))
    print("wrote %s/ (%d pngs)" % (set_dir, len(ICONSET)))

    icns_path = os.path.join(args.outdir, "AppIcon.icns")
    if sys.platform == "darwin":
        subprocess.run(["iconutil", "-c", "icns", set_dir, "-o", icns_path], check=True)
        print("wrote %s (%d bytes)" % (icns_path, os.path.getsize(icns_path)))
    else:
        print("not on macOS; on a Mac run:  iconutil -c icns %s -o %s"
              % (set_dir, icns_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
