"""Generate the app icon (pure stdlib PNG writer + macOS sips/iconutil).

Usage:  python packaging/make_icon.py
Output: packaging/icon.icns  (+ packaging/icon.png)
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import zlib

SIZE = 1024
SS = 3  # supersampling factor


def _png_bytes(width: int, height: int, rgba: bytearray) -> bytes:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _rounded_rect(x, y, w, h, r, px, py) -> bool:
    if px < x or px > x + w or py < y or py > y + h:
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _bolt_polygon(cx: float, cy: float, scale: float):
    pts = [(-0.18, -0.46), (0.20, -0.46), (-0.02, -0.06), (0.20, -0.06),
           (-0.20, 0.48), (-0.02, 0.06), (-0.24, 0.06)]
    return [(cx + px * scale, cy + py * scale) for px, py in pts]


def _in_polygon(pts, px, py) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xin = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xin:
                inside = not inside
    return inside


def render() -> bytearray:
    w = h = SIZE * SS
    buf = bytearray(w * h * 4)
    margin = 0.055 * w
    radius = 0.235 * w
    cx, cy = w / 2, h / 2
    bolt = _bolt_polygon(cx, cy * 0.98, w * 0.62)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            if not _rounded_rect(margin, margin, w - 2 * margin, h - 2 * margin, radius, x, y):
                continue
            t = (x + y) / (2 * w)
            r = int(24 + 20 * t)
            g = int(150 + 60 * t)
            b = int(220 + 30 * t)
            if _in_polygon(bolt, x, y):
                r, g, b = 245, 250, 255
            buf[i:i + 4] = bytes((r, g, b, 255))
    return buf


def downsample(buf: bytearray, src: int, dst: int) -> bytearray:
    out = bytearray(dst * dst * 4)
    factor = src // dst
    area = factor * factor
    for y in range(dst):
        for x in range(dst):
            r = g = b = a = 0
            for dy in range(factor):
                base = ((y * factor + dy) * src + x * factor) * 4
                for dx in range(factor):
                    i = base + dx * 4
                    r += buf[i]
                    g += buf[i + 1]
                    b += buf[i + 2]
                    a += buf[i + 3]
            o = (y * dst + x) * 4
            out[o:o + 4] = bytes((r // area, g // area, b // area, a // area))
    return out


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    png_path = os.path.join(here, "icon.png")
    icns_path = os.path.join(here, "icon.icns")
    big = render()
    small = downsample(big, SIZE * SS, SIZE)
    with open(png_path, "wb") as fh:
        fh.write(_png_bytes(SIZE, SIZE, small))
    print("wrote", png_path)

    if sys.platform != "darwin":
        print("非 macOS：跳过 icns 生成（仅输出 icon.png）")
        return 0

    iconset = os.path.join(here, "icon.iconset")
    os.makedirs(iconset, exist_ok=True)
    specs = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
             (256, 1), (256, 2), (512, 1), (512, 2)]
    for size, scale in specs:
        px = size * scale
        name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        subprocess.run(["sips", "-z", str(px), str(px), png_path,
                        "--out", os.path.join(iconset, name)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns_path], check=True)
    print("wrote", icns_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
