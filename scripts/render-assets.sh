#!/bin/sh
# Rebuild the PNGs in docs/assets/ from the drop page, the banner SVGs and hero.html.
# Needs Google Chrome and python3 (3.10+); set CHROME or PYTHON to override.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/docs/assets"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

shot() { # shot <url> <width> <height> <out.png>
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
    --default-background-color=00000000 --window-size="$2,$3" --screenshot="$4" "$1" >/dev/null 2>&1
}

crop() { # crop <png> <width> <height>, from the top-left corner
  "$PYTHON" - "$@" <<'PY'
import struct, sys, zlib
path, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
data = open(path, "rb").read()
chunks, pos = [], 8
while pos < len(data):
    n = struct.unpack(">I", data[pos:pos + 4])[0]
    chunks.append((data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]))
    pos += 12 + n
ihdr = chunks[0][1]
width, height, depth, ctype = struct.unpack(">IIBB", ihdr[:10])
bpp = {2: 3, 6: 4}[ctype] * depth // 8
raw = zlib.decompress(b"".join(c for t, c in chunks if t == b"IDAT"))
stride = width * bpp
rows, prev = [], bytearray(stride)
for y in range(height):
    f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
    for i in range(stride):
        a = line[i - bpp] if i >= bpp else 0
        b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
        if f == 1: line[i] = (line[i] + a) & 255
        elif f == 2: line[i] = (line[i] + b) & 255
        elif f == 3: line[i] = (line[i] + (a + b) // 2) & 255
        elif f == 4:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    rows.append(bytes(line)); prev = line
out = b"".join(b"\0" + r[:w * bpp] for r in rows[:h])
def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, 0))
                       + chunk(b"IDAT", zlib.compress(out, 9)) + chunk(b"IEND", b""))
PY
}

# Drop page: the real template with a demo request, at a 390x844 phone viewport.
for scheme in light dark; do
  PYTHONPATH="$ROOT" "$PYTHON" - "$scheme" "$WORK" <<'PY'
import sys, time
from secretdrop.pages import drop_page
from secretdrop.store import DropRequest
scheme, work = sys.argv[1], sys.argv[2]
req = DropRequest(id="demo", dest="/home/you/.openclaw/credentials/telegram-bot.token",
                  label="Telegram bot token", mode="file", key=None,
                  created_at=time.time(), expires_at=time.time() + 1800)
html = drop_page(req).decode().replace("color-scheme: light dark;", f"color-scheme: {scheme};")
open(f"{work}/drop-{scheme}.html", "w").write(html)
open(f"{work}/frame-{scheme}.html", "w").write(
    '<html><body style="margin:0">'
    f'<iframe src="drop-{scheme}.html" style="border:0;width:390px;height:844px;display:block"></iframe>'
    "</body></html>")
PY
  shot "file://$WORK/frame-$scheme.html" 800 900 "$ASSETS/drop-$scheme.png"
  crop "$ASSETS/drop-$scheme.png" 780 1688
done

# Banner: SVG text is drawn with the renderer's fonts, so ship a PNG.
for scheme in light dark; do
  printf '<html><body style="margin:0"><img src="%s" width="1280" height="320" style="display:block"></body></html>' \
    "$ASSETS/banner-$scheme.svg" > "$WORK/banner-$scheme.html"
  shot "file://$WORK/banner-$scheme.html" 1280 320 "$ASSETS/banner-$scheme.png"
done

shot "file://$ASSETS/hero.html" 1200 627 "$ASSETS/hero.png"

echo "rendered: $(cd "$ASSETS" && ls *.png | tr '\n' ' ')"
