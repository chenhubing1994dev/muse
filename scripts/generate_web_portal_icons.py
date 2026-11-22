"""Generate dependency-free PNG icons for the Muse server PWA and iOS home screen."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


def _blend(base: tuple[int, int, int], overlay: tuple[int, int, int], alpha: float) -> tuple[int, int, int]:
    alpha = min(1.0, max(0.0, alpha))
    return tuple(round(base[i] * (1.0 - alpha) + overlay[i] * alpha) for i in range(3))


def _inside_polygon(x: float, y: float, points: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def _inside_rounded_square(x: float, y: float, size: int, radius: float) -> bool:
    dx = max(radius - x, 0.0, x - (size - radius))
    dy = max(radius - y, 0.0, y - (size - radius))
    return dx * dx + dy * dy <= radius * radius


def _render_rgba(size: int, scale: int = 3) -> bytes:
    side = size * scale
    pixels = bytearray(side * side * 4)
    star = (
        (0.0, -0.32), (0.052, -0.060), (0.32, 0.0), (0.052, 0.060),
        (0.0, 0.32), (-0.052, 0.060), (-0.32, 0.0), (-0.052, -0.060),
    )
    for py in range(side):
        ny = (py + 0.5) / side - 0.5
        for px in range(side):
            x = px + 0.5
            y = py + 0.5
            if not _inside_rounded_square(x, y, side, side * 0.22):
                continue
            nx = x / side - 0.5
            diagonal = min(1.0, max(0.0, nx + ny + 0.5))
            base = _blend((27, 49, 88), (8, 13, 29), diagonal)
            distance = math.hypot(nx, ny)
            glow = max(0.0, 1.0 - distance / 0.47) ** 2 * 0.25
            color = _blend(base, (124, 232, 239), glow)

            outer = abs(distance - 0.38) <= 1.8 / size
            inner = abs(distance - 0.285) <= 1.8 / size
            if outer:
                color = _blend(color, (216, 194, 126), 0.62)
            if inner:
                color = _blend(color, (124, 232, 239), 0.55)
            if _inside_polygon(nx, ny, star):
                color = (216, 194, 126)
            if abs(nx) <= 1.0 / size and abs(ny) <= 0.22:
                color = _blend(color, (124, 232, 239), 0.45)
            if abs(ny) <= 1.0 / size and abs(nx) <= 0.22:
                color = _blend(color, (124, 232, 239), 0.45)
            if distance <= 0.035:
                color = (240, 251, 255)

            offset = (py * side + px) * 4
            pixels[offset:offset + 4] = bytes((*color, 255))

    if scale == 1:
        return bytes(pixels)
    output = bytearray(size * size * 4)
    samples = scale * scale
    for y in range(size):
        for x in range(size):
            sums = [0, 0, 0, 0]
            for sy in range(scale):
                for sx in range(scale):
                    source = (((y * scale + sy) * side) + (x * scale + sx)) * 4
                    for channel in range(4):
                        sums[channel] += pixels[source + channel]
            target = (y * size + x) * 4
            output[target:target + 4] = bytes(round(value / samples) for value in sums)
    return bytes(output)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _write_png(path: Path, size: int) -> None:
    rgba = _render_rgba(size)
    stride = size * 4
    raw = b"".join(b"\x00" + rgba[row * stride:(row + 1) * stride] for row in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(raw, 9)) + _png_chunk(b"IEND", b"")
    path.write_bytes(data)


def generate(output_dir: str | Path = "web-portal") -> list[Path]:
    root = Path(output_dir)
    if root.is_symlink():
        raise ValueError("PWA icon output directory must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("PWA icon output path must be a directory")
    outputs = (
        ("apple-touch-icon.png", 180),
        ("icon-192.png", 192),
        ("icon-512.png", 512),
    )
    paths = []
    for filename, size in outputs:
        target = root / filename
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"PWA icon target must be a regular non-symlink file: {filename}")
        _write_png(target, size)
        paths.append(target)
    return paths


if __name__ == "__main__":
    for output in generate():
        print(f"generated {output.as_posix()}")
