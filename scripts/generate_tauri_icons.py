from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "desktop" / "src-tauri" / "icons"


def chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def pixel(canvas: list[list[tuple[int, int, int, int]]], x: int, y: int, color: tuple[int, int, int, int]) -> None:
    h, w = len(canvas), len(canvas[0])
    if 0 <= x < w and 0 <= y < h:
        canvas[y][x] = color


def disc(canvas, cx: float, cy: float, radius: float, color) -> None:
    r = max(1, math.ceil(radius))
    for y in range(math.floor(cy) - r, math.ceil(cy) + r + 1):
        for x in range(math.floor(cx) - r, math.ceil(cx) + r + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                pixel(canvas, x, y, color)


def line(canvas, x0: float, y0: float, x1: float, y1: float, width: float, color) -> None:
    steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * 1.5))
    for i in range(steps + 1):
        t = i / steps
        disc(canvas, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, width / 2, color)


def render_png(size: int) -> bytes:
    bg = (7, 12, 28, 255)
    gold = (226, 190, 112, 255)
    pale = (250, 232, 183, 255)
    canvas = [[bg for _ in range(size)] for _ in range(size)]
    c = (size - 1) / 2
    scale = size / 512
    ring_r = 184 * scale
    stroke = max(1.0, 7 * scale)

    # Eightfold celestial ring and radial compass.
    for i in range(8):
        a = math.pi * i / 4 - math.pi / 2
        inner = 78 * scale
        outer = (160 if i % 2 == 0 else 136) * scale
        line(canvas, c + math.cos(a) * inner, c + math.sin(a) * inner,
             c + math.cos(a) * outer, c + math.sin(a) * outer, 5 * scale, gold)
        disc(canvas, c + math.cos(a) * (ring_r - 2 * scale),
             c + math.sin(a) * (ring_r - 2 * scale), 5 * scale, pale)

    # Double ring, with small cardinal markers.
    for radius, width in ((ring_r, stroke), (ring_r - 18 * scale, 2.5 * scale)):
        points = 720
        for i in range(points):
            a = 2 * math.pi * i / points
            disc(canvas, c + math.cos(a) * radius, c + math.sin(a) * radius,
                 width / 2, gold)
    for i in range(4):
        a = math.pi * i / 2
        line(canvas, c + math.cos(a) * (ring_r + 8 * scale),
             c + math.sin(a) * (ring_r + 8 * scale),
             c + math.cos(a) * (ring_r + 25 * scale),
             c + math.sin(a) * (ring_r + 25 * scale), 5 * scale, pale)

    # Central diamond and luminous core.
    diamond = [(c, c - 70 * scale), (c + 48 * scale, c),
               (c, c + 70 * scale), (c - 48 * scale, c)]
    for p, q in zip(diamond, diamond[1:] + diamond[:1]):
        line(canvas, *p, *q, 6 * scale, pale)
    disc(canvas, c, c, 16 * scale, pale)
    disc(canvas, c, c, 7 * scale, bg)

    raw = b"".join(b"\x00" + bytes(channel for rgba in row for channel in rgba) for row in canvas)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    assets = {
        "32x32.png": render_png(32),
        "128x128.png": render_png(128),
        "128x128@2x.png": render_png(256),
        "icon.png": render_png(512),
    }
    for name, data in assets.items():
        (ROOT / name).write_bytes(data)

    # PNG-compressed ICO frame (supported by modern Windows).
    png = assets["icon.png"]
    ico_header = struct.pack("<HHH", 0, 1, 1)
    ico_entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    (ROOT / "icon.ico").write_bytes(ico_header + ico_entry + png)

    # ICNS PNG chunks: 256px and 512px representations.
    icns_chunks = b""
    for chunk_type, name in ((b"ic08", "128x128@2x.png"), (b"ic09", "icon.png")):
        payload = assets[name]
        icns_chunks += chunk_type + struct.pack(">I", len(payload) + 8) + payload
    (ROOT / "icon.icns").write_bytes(b"icns" + struct.pack(">I", len(icns_chunks) + 8) + icns_chunks)

    # Validate PNG dimensions/signatures and container headers before returning.
    for name, expected in (("32x32.png", 32), ("128x128.png", 128),
                           ("128x128@2x.png", 256), ("icon.png", 512)):
        data = (ROOT / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", data[16:24]) == (expected, expected)
    assert (ROOT / "icon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert (ROOT / "icon.icns").read_bytes()[:4] == b"icns"
    print(f"Generated and validated {len(assets) + 2} Tauri icon assets in {ROOT}")


if __name__ == "__main__":
    main()
