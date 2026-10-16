#!/usr/bin/env python3
"""EBB test plate: dot-pitch test coupon as an SVG for Bambu Studio (laser).

Answers the handoff's open question "what is the smallest reliable dot pitch",
plus the minimum legible text size and chart line widths, before committing to
the full plate. Contents (all real size, mm):

  - dot grids at 0.12 / 0.15 / 0.18 / 0.20 / 0.22 / 0.25 mm pitch, each with
    dots at 50% and 70% of the pitch; left half every dot set, right half a
    checkerboard (worst case for neighbouring dots merging)
  - one full 64x64 data tile (the plate's first real tile) at 0.15 and 0.20 mm
  - text samples 1.0-2.5 mm
  - stroke widths 0.10-0.30 mm, a dotted line and the s.e. hatch from the charts

Usage:
  venv/bin/python ebb_plate_coupon.py [--out plate_coupon.svg]
"""
import argparse, math
from pathlib import Path

from svg_tools import SvgCanvas
from ebb_plate_render import load_series, serialize, tile_modules, PAYLOAD_BYTES, TILE

PITCHES = [0.12, 0.15, 0.18, 0.20, 0.22, 0.25]
DOT_FRACTIONS = [0.5, 0.7]
PATCH = 6.0      # mm, square patch per pitch/size
W, H = 96, 72    # coupon size, mm


def grid_patch(c, x, y, pitch, frac):
    n = int(PATCH / pitch)
    half = n // 2
    centers = []
    for r in range(n):
        for k in range(n):
            if k < half or (r + k) % 2 == 0:   # left: all dots; right: checkerboard
                centers.append((x + (k + 0.5) * pitch, y + (r + 0.5) * pitch))
    c.squares(centers, pitch * frac)


def tile(c, x, y, pitch, modules, frac=0.7):
    centers = [(x + (col + 0.5) * pitch, y + (row + 0.5) * pitch)
               for row in range(TILE) for col in range(TILE) if modules[row][col]]
    c.squares(centers, pitch * frac)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent
    ap.add_argument("--data", default=str(here / "data"))
    ap.add_argument("--out", default=str(here / "plate_coupon.svg"))
    args = ap.parse_args()

    c = SvgCanvas(W, H, "EBB plate dot-pitch coupon v1")
    c.rect(0.5, 0.5, W - 0.5, H - 0.5, 0.2)
    c.text(3, 2.5, "EBB DOT-PITCH COUPON v1", 2.4, bold=True)
    c.text(W - 3, 3, "pitch mm · dot size % of pitch", 1.4, anchor="ra")

    # dot grids: columns = pitch, rows = dot size
    gx0, gy0, step = 12, 9.5, PATCH + 7.5
    for i, pitch in enumerate(PITCHES):
        c.text(gx0 + i * step + PATCH / 2, gy0, f"{pitch:.2f}", 1.6, anchor="ma")
    for j, frac in enumerate(DOT_FRACTIONS):
        y = gy0 + 3 + j * (PATCH + 3)
        c.text(3, y + PATCH / 2, f"{int(frac * 100)}%", 1.6, anchor="lm")
        for i, pitch in enumerate(PITCHES):
            grid_patch(c, gx0 + i * step, y, pitch, frac)

    # real tile (first tile of the plate payload) at two pitches
    S = load_series(Path(args.data) / "plate_series.csv")
    modules = tile_modules(1, 0, 0, serialize(S)[:PAYLOAD_BYTES])
    ty = 34
    c.text(3, ty, "TILE 64×64 (plate tile 0)", 1.6, bold=True)
    tile(c, 3, ty + 3, 0.15, modules)
    c.text(3 + 64 * 0.15 / 2, ty + 3 + 64 * 0.15 + 1, "0.15", 1.6, anchor="ma")
    tile(c, 16, ty + 3, 0.20, modules)
    c.text(16 + 64 * 0.20 / 2, ty + 3 + 64 * 0.20 + 1, "0.20", 1.6, anchor="ma")

    # text sizes
    tx, y = 34, 34
    c.text(tx, y, "TEXT (em height, mm)", 1.6, bold=True)
    y += 3.2
    sample = "CO₂ 427.55 ppm · 10²² J ±0.29 · 1958–2026"
    for size in (1.0, 1.25, 1.5, 2.0, 2.5):
        c.text(tx, y, f"{size:g}", 1.4)
        c.text(tx + 5, y, sample, size)
        y += size * 1.35 + 0.8
    c.text(tx, y, "1.5B", 1.4)
    c.text(tx + 5, y, sample, 1.5, bold=True)

    # line widths + chart textures
    lx, ly = 3, 57
    c.text(lx, ly, "LINES (stroke mm)", 1.6, bold=True)
    for k, wdt in enumerate((0.10, 0.15, 0.20, 0.25, 0.30)):
        yy = ly + 4 + k * 2.2
        c.text(lx, yy, f"{wdt:.2f}", 1.4, anchor="lm")
        c.line([(lx + 7, yy), (lx + 30, yy)], wdt)
    c.text(40, ly, "DOTTED (r 0.18, 0.6 apart)", 1.4)
    for k in range(40):
        c.dot(40 + k * 0.6, ly + 4.5, 0.18)
    c.text(40, ly + 7, "S.E. HATCH (0.08 every 0.5)", 1.4)
    for k in range(48):
        x = 40 + k * 0.5
        c.line([(x, ly + 10), (x, ly + 13.5)], 0.08)

    c.save(args.out)
    print(f"wrote {args.out} ({W}×{H} mm)")


if __name__ == "__main__":
    main()
