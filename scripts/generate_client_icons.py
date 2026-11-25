"""Generate the Muse 深空星枢 client icons with pure Python (``struct`` + ``zlib`` only).

Writes Tauri v2-compliant RGBA assets into ``src-tauri/icons/``:
``32x32.png``, ``128x128.png``, ``128x128@2x.png``, ``icon.png`` (512), ``icon.ico``
(16–256, BMP entries ≤64 px + PNG entries ≥128 px) and ``icon.icns`` (PNG-based ic07–ic14).

Usage: ``python scripts/generate_client_icons.py [--out src-tauri/icons]``
"""
from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "src-tauri" / "icons"

# deterministic small stars (unit coordinates, radius, brightness)
_STARS = ((0.22, 0.27, 0.018, 0.9), (0.74, 0.21, 0.014, 0.8), (0.81, 0.66, 0.016, 0.85),
          (0.30, 0.78, 0.012, 0.7), (0.58, 0.83, 0.010, 0.6), (0.16, 0.55, 0.010, 0.6),
          (0.66, 0.36, 0.008, 0.5), (0.42, 0.18, 0.008, 0.5))


def _clamp(value: float) -> int:
    return 0 if value <= 0 else 255 if value >= 1 else int(value * 255 + 0.5)


def _sample(u: float, v: float) -> tuple[float, float, float, float]:
    """Colour of one sample in unit space (premultiplied-free RGBA floats)."""
    dx, dy = u - 0.5, v - 0.5
    radius = math.hypot(dx, dy)
    # rounded-square silhouette (superellipse) with soft edge
    n = 5.0
    shape = (abs(dx) / 0.47) ** n + (abs(dy) / 0.47) ** n
    if shape > 1.0:
        return 0.0, 0.0, 0.0, 0.0
    # deep-space radial gradient
    t = min(1.0, radius / 0.62)
    r = 0.11 * (1 - t) + 0.02 * t
    g = 0.16 * (1 - t) + 0.02 * t
    b = 0.45 * (1 - t) + 0.08 * t
    # nebula glow
    glow = math.exp(-((dx + 0.06) ** 2 + (dy + 0.08) ** 2) / 0.03)
    r, g, b = r + 0.35 * glow, g + 0.20 * glow, b + 0.55 * glow
    # orbit ring (tilted ellipse)
    angle = math.radians(-24)
    ex = dx * math.cos(angle) - dy * math.sin(angle)
    ey = dx * math.sin(angle) + dy * math.cos(angle)
    ring = abs(math.hypot(ex / 0.38, ey / 0.16) - 1.0)
    ring_alpha = max(0.0, 1.0 - ring / 0.06)
    r, g, b = r + 0.95 * ring_alpha * 0.8, g + 0.80 * ring_alpha * 0.8, b + 0.35 * ring_alpha * 0.8
    # four-point central star
    ax, ay = abs(dx), abs(dy)
    star = max(0.0, 1.0 - (ax * ay) * 900.0) * max(0.0, 1.0 - max(ax, ay) / 0.30)
    core = math.exp(-(radius ** 2) / 0.004)
    light = min(1.0, star * 0.9 + core)
    r, g, b = r + light, g + light * 0.97, b + light * 0.85
    # small stars
    for sx, sy, sr, sb in _STARS:
        d = math.hypot(u - sx, v - sy)
        if d < sr * 2.5:
            k = max(0.0, 1.0 - d / sr) * sb
            r, g, b = r + k, g + k, b + k
    edge = max(0.0, min(1.0, (1.0 - shape) * 12.0))
    return r, g, b, edge


def render(size: int) -> bytes:
    """Return raw RGBA rows (top-down) for a square icon of ``size`` px."""
    ss = 4 if size <= 64 else 2 if size <= 256 else 1
    out = bytearray()
    step = 1.0 / (size * ss)
    for y in range(size):
        for x in range(size):
            acc_r = acc_g = acc_b = acc_a = 0.0
            for sy in range(ss):
                for sx in range(ss):
                    u = (x * ss + sx + 0.5) * step
                    v = (y * ss + sy + 0.5) * step
                    r, g, b, a = _sample(u, v)
                    acc_r += r * a
                    acc_g += g * a
                    acc_b += b * a
                    acc_a += a
            n = ss * ss
            alpha = acc_a / n
            if alpha > 0:
                out += bytes((_clamp(acc_r / acc_a), _clamp(acc_g / acc_a), _clamp(acc_b / acc_a), _clamp(alpha)))
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def png_bytes(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    row = size * 4
    raw = b"".join(b"\x00" + rgba[y * row:(y + 1) * row] for y in range(size))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def bmp_icon_entry(size: int, rgba: bytes) -> bytes:
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    row = size * 4
    pixels = bytearray()
    for y in range(size - 1, -1, -1):  # bottom-up BGRA
        line = rgba[y * row:(y + 1) * row]
        for x in range(size):
            r, g, b, a = line[x * 4:x * 4 + 4]
            pixels += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4
    return header + bytes(pixels) + b"\x00" * (mask_row * size)


def ico_bytes(images: dict[int, bytes]) -> bytes:
    sizes = sorted(images)
    entries, blobs = [], []
    offset = 6 + 16 * len(sizes)
    for size in sizes:
        blob = png_bytes(size, images[size]) if size >= 128 else bmp_icon_entry(size, images[size])
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(blobs)


def icns_bytes(pngs: dict[str, bytes]) -> bytes:
    body = b"".join(kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data for kind, data in pngs.items())
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def generate(out_dir: Path = DEFAULT_OUT) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    needed = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
    raw = {size: render(size) for size in needed}
    png = {size: png_bytes(size, raw[size]) for size in needed}
    files = {
        "32x32.png": png[32],
        "128x128.png": png[128],
        "128x128@2x.png": png[256],
        "icon.png": png[512],
        "icon.ico": ico_bytes({s: raw[s] for s in (16, 24, 32, 48, 64, 128, 256)}),
        "icon.icns": icns_bytes({"ic11": png[32], "ic12": png[64], "ic07": png[128], "ic13": png[256],
                                 "ic08": png[256], "ic14": png[512], "ic09": png[512], "ic10": png[1024]}),
    }
    written = {}
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
        written[name] = len(data)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    for name, size in generate(args.out).items():
        print(f"{name}: {size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
