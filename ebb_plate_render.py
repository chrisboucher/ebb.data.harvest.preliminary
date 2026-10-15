#!/usr/bin/env python3
"""EBB test plate: render a bitmap preview of the 300 x 300 mm plate.

Layout follows EBB_plate_handoff.md section 4 (PROPOSED): title/primer band,
2 x 2 charts, and a 28 x 10 band of 64 x 64-module binary tiles (format v1:
finder L, clock tracks, duplicated header with CRC-16/CCITT-FALSE, payload
masked with (row + col) mod 2).

PREVIEW ONLY: the tile payload is the real plate_series.csv data as
fixed-width scaled integers, stored 3x in separated parts of the band, but
WITHOUT Reed-Solomon (stripe sizing is still an open decision). Tiles not
used by a data copy show only finder/clock/header — that is where RS parity
would go.

Usage:
  python3 ebb_plate_render.py [--data data] [--out plate_preview.png] [--px-per-mm 20]

Needs Pillow.
"""
import argparse, csv, math, struct
from datetime import date
from pathlib import Path
from statistics import mean

from PIL import Image, ImageDraw, ImageFont

PLATE = (300, 300)          # mm
ACTIVE = (280, 260)         # mm, laser-reachable
ACTIVE_ORIGIN = (10, 20)    # mm, top-left of active area on the plate
STEEL, MARK = 196, 28       # grey levels: bare steel, black oxide mark
PITCH = 0.15                # mm per module
TILE, TILE_PITCH = 64, 66   # modules
BAND_COLS, BAND_ROWS = 28, 10
PAYLOAD_BYTES = 465         # rows 3-62 x cols 1-62 = 3720 bits
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


# ── data ────────────────────────────────────────────────────────────────────

def load_series(path):
    s = {}
    for r in csv.DictReader(open(path)):
        s.setdefault(r["series"], []).append(
            (float(r["t"]), float(r["value"]), float(r["unc"]) if r["unc"] else None))
    return s


def annual_means(recs, full_only=True):
    by = {}
    for t, v, _ in recs:
        by.setdefault(int(t), []).append(v)
    return sorted((y + 0.5, mean(v)) for y, v in by.items() if not full_only or len(v) >= 12)


# ── payload (preview format, not final) ─────────────────────────────────────

# id, csv series, value column, int type, scale. Per handoff section 5 table.
PAYLOAD_SERIES = [
    (1, "co2", 1, "H", 100),                 # uint16 x100 ppm
    (2, "temp_anomaly", 1, "h", 1000),       # int16 x1000 degC
    (3, "sea_level_recon", 1, "h", 10),      # int16 x10 mm
    (4, "sea_level_sat", 1, "h", 10),        # int16 x10 mm (irregular -> explicit t)
    (5, "ocean_heat_0_700m", 1, "h", 100),   # int16 x100 1e22 J
    (6, "ocean_heat_0_700m", 2, "h", 100),   # its standard error
]


def serialize(series):
    """Simple self-describing byte stream: magic, then one block per series.
    Regular series: start, step, count, values. Irregular: count, (t, value) pairs.
    t is int32 decimal-year x 10000."""
    out = bytearray(b"EBB1")
    for sid, name, col, typ, scale in PAYLOAD_SERIES:
        recs = [r for r in series[name] if r[col] is not None]
        ts = [r[0] for r in recs]
        steps = {round(b - a, 3) for a, b in zip(ts, ts[1:])}
        regular = len(steps) == 1
        out += struct.pack(">BBcH", sid, 0 if regular else 1, typ.encode(), scale)
        vals = [round(r[col] * scale) for r in recs]
        if regular:
            out += struct.pack(">iiH", round(ts[0] * 1e4), round(steps.pop() * 1e4), len(vals))
            out += struct.pack(f">{len(vals)}{typ}", *vals)
        else:
            out += struct.pack(">H", len(vals))
            for t, v in zip(ts, vals):
                out += struct.pack(f">i{typ}", round(t * 1e4), v)
    return bytes(out)


def crc16_ccitt_false(bits):
    crc = 0xFFFF
    for b in bits:
        top = (crc >> 15) & 1
        crc = (crc << 1) & 0xFFFF
        if top ^ b:
            crc ^= 0x1021
    return crc


def to_bits(value, n):
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def tile_modules(plate_id, trow, tcol, payload):
    """64x64 grid of 0/1 (1 = engraved dot) for one tile."""
    g = [[0] * TILE for _ in range(TILE)]
    for i in range(TILE):
        g[i][0] = 1                    # finder: left column
        g[TILE - 1][i] = 1             # finder: bottom row
        g[0][i] = 1 if i % 2 == 0 else 0           # clock: top row
        g[i][TILE - 1] = 1 if i % 2 == 0 else 0    # clock: right column
    hdr = to_bits(plate_id, 12) + to_bits(trow, 8) + to_bits(tcol, 8) + to_bits(1, 8) + to_bits(0, 10)
    hdr += to_bits(crc16_ccitt_false(hdr), 16)            # 62 bits
    for c in range(62):
        g[1][c + 1] = g[2][c + 1] = hdr[c]
    if payload is not None:
        bits = [b for byte in payload.ljust(PAYLOAD_BYTES, b"\0") for b in to_bits(byte, 8)]
        k = 0
        for r in range(3, 63):
            for c in range(1, 63):
                g[r][c] = bits[k] ^ ((r + c) % 2)
                k += 1
    return g


# ── drawing ─────────────────────────────────────────────────────────────────

class Plate:
    def __init__(self, ppm):
        self.ppm = ppm
        self.img = Image.new("L", (round(PLATE[0] * ppm), round(PLATE[1] * ppm)), STEEL)
        self.d = ImageDraw.Draw(self.img)
        self._fonts = {}

    def px(self, x, y):  # active-area mm -> pixels
        return ((ACTIVE_ORIGIN[0] + x) * self.ppm, (ACTIVE_ORIGIN[1] + y) * self.ppm)

    def font(self, size_mm, bold=False):
        k = (size_mm, bold)
        if k not in self._fonts:
            self._fonts[k] = ImageFont.truetype(FONT_B if bold else FONT, max(6, round(size_mm * self.ppm)))
        return self._fonts[k]

    def w(self, mm):
        return max(1, round(mm * self.ppm))

    def text(self, x, y, s, size, bold=False, anchor="la"):
        self.d.text(self.px(x, y), s, fill=MARK, font=self.font(size, bold), anchor=anchor)

    def line(self, pts, width=0.25):
        self.d.line([self.px(*p) for p in pts], fill=MARK, width=self.w(width), joint="curve")

    def rect(self, x0, y0, x1, y1, width=0.2):
        self.d.rectangle([self.px(x0, y0), self.px(x1, y1)], outline=MARK, width=self.w(width))


def nice_ticks(lo, hi, n=5):
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 6))
        t += step
    return out


def chart(p, x, y, w, h, title, source, unit, layers, x_range):
    """layers: list of (style, points[, band]) with style in solid|dotted|steps."""
    p.rect(x, y, x + w, y + h, 0.2)
    p.text(x + 2, y + 1.5, title, 2.6, bold=True)
    p.text(x + w - 2, y + 1.9, source, 1.5, anchor="ra")
    L, R, T, B = x + 13, x + w - 3, y + 7, y + h - 7
    vals = [v for _, pts, *rest in layers for _, v in pts]
    for _, _, *rest in layers:
        if rest:
            vals += [lo for _, lo, _ in rest[0]] + [hi for _, _, hi in rest[0]]
    vlo, vhi = min(vals), max(vals)
    pad = (vhi - vlo) * 0.06
    vlo, vhi = vlo - pad, vhi + pad
    t0, t1 = x_range
    X = lambda t: L + (t - t0) / (t1 - t0) * (R - L)
    Y = lambda v: B - (v - vlo) / (vhi - vlo) * (B - T)

    # axes, ticks, labels
    p.line([(L, T), (L, B), (R, B)], 0.2)
    for v in nice_ticks(vlo, vhi):
        p.line([(L - 1, Y(v)), (L, Y(v))], 0.15)
        lbl = f"{v:g}"
        p.text(L - 1.5, Y(v), lbl, 1.6, anchor="rm")
    for yr in range(int(math.ceil(t0 / 20) * 20), int(t1) + 1, 20):
        p.line([(X(yr), B), (X(yr), B + 1)], 0.15)
        p.text(X(yr), B + 1.5, str(yr), 1.6, anchor="ma")
    p.text(x + 2, (T + B) / 2, unit, 1.6, anchor="lm")

    for style, pts, *rest in layers:
        if rest:  # standard-error band: vertical hatch every 0.5 mm
            band = rest[0]
            for i in range(len(band) - 1):
                (ta, lo, hi), (tb, _, _) = band[i], band[i + 1]
                xa = X(ta)
                while xa < X(tb):
                    p.line([(xa, Y(lo)), (xa, Y(hi))], 0.08)
                    xa += 0.5
        xy = [(X(t), Y(v)) for t, v in pts]
        if style == "solid":
            p.line(xy, 0.3)
        elif style == "steps":
            st = []
            for (xa, ya), (xb, _) in zip(xy, xy[1:] + [(xy[-1][0] + (xy[-1][0] - xy[-2][0]), 0)]):
                st += [(xa, ya), (xb, ya)]
            p.line(st, 0.3)
        elif style == "dotted":
            last = -1e9
            for xx, yy in xy:
                if xx - last >= 0.6:
                    r = 0.18
                    p.d.ellipse([p.px(xx - r, yy - r), p.px(xx + r, yy + r)], fill=MARK)
                    last = xx


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent
    ap.add_argument("--data", default=str(here / "data"))
    ap.add_argument("--out", default=str(here / "plate_preview.png"))
    ap.add_argument("--px-per-mm", type=float, default=20)
    args = ap.parse_args()

    S = load_series(Path(args.data) / "plate_series.csv")
    p = Plate(args.px_per_mm)
    last_t = max(t for recs in S.values() for t, _, _ in recs)
    data_to = f"{int(last_t)}-{int((last_t % 1) * 12) + 1:02d}"

    # plate edge + active-area registration corners
    p.d.rectangle([0, 0, p.img.width - 1, p.img.height - 1], outline=MARK, width=p.w(0.3))
    for cx, cy in [(0, 0), (ACTIVE[0], 0), (0, ACTIVE[1]), ACTIVE]:
        sx, sy = (1 if cx == 0 else -1), (1 if cy == 0 else -1)
        p.line([(cx, cy + 4 * sy), (cx, cy), (cx + 4 * sx, cy)], 0.3)

    # ── title + primer band (0-30 mm) ──
    p.text(2, 2, "EARTH'S BLACK BOX  ·  CLIMATE RECORD  ·  TEST PLATE", 4.2, bold=True)
    p.text(ACTIVE[0] - 2, 3, f"data to {data_to}  ·  rendered {date.today().isoformat()}", 1.7, anchor="ra")
    primer = [
        "CHARTS: atmospheric CO₂ (ppm, NOAA GML Mauna Loa monthly) · global temperature anomaly (°C vs 1901–2000, NOAA NCEI, annual means) ·",
        "global mean sea level (mm; CSIRO tide-gauge reconstruction dotted, AVISO satellite altimetry solid) · ocean heat content 0–700 m (10²² J, NOAA NCEI WOA22, hatched = ±1 s.e.).",
        "TILES: each square is 64×64 dots at 0.15 mm. Dot = 1, blank = 0. Solid left column + bottom row = orientation; top row + right column alternate = clock.",
        "Rows 1–2: header (plate 12 b, tile row 8 b, tile col 8 b, version 8 b, reserved 10 b, CRC-16/CCITT-FALSE 16 b), written twice.",
        "Rows 3–62, cols 1–62: 465 data bytes, read row by row, most significant bit first, each bit XOR ((row + col) mod 2). Every dataset is stored 3 times.",
    ]
    for i, line in enumerate(primer):
        p.text(2, 9.5 + i * 3.6, line, 1.75)

    # ── charts (2 x 2) ──
    cw, ch, gut = 137, 56, 6
    y1, y2 = 30 + gut, 30 + gut + ch + gut
    co2 = [(t, v) for t, v, _ in S["co2"]]
    temp = annual_means(S["temp_anomaly"])
    temp = [(t, v) for t, v in temp if t >= 1880]
    recon = [(t, v) for t, v, _ in S["sea_level_recon"]]
    sat = [(t, v) for t, v, _ in S["sea_level_sat"]]
    ohc = S["ocean_heat_0_700m"]
    ohc_pts = [(t, v) for t, v, _ in ohc]
    ohc_band = [(t, v - (u or 0), v + (u or 0)) for t, v, u in ohc]
    end = math.ceil(last_t)
    chart(p, 0, y1, cw, ch, "CO₂", "NOAA GML · Mauna Loa", "ppm", [("solid", co2)], (1955, end))
    chart(p, cw + gut, y1, cw, ch, "TEMPERATURE ANOMALY", "NOAA NCEI · vs 1901–2000", "°C", [("solid", temp)], (1880, end))
    chart(p, 0, y2, cw, ch, "SEA LEVEL", "CSIRO recon · AVISO satellite", "mm",
          [("dotted", recon), ("solid", sat)], (1880, end))
    chart(p, cw + gut, y2, cw, ch, "OCEAN HEAT 0–700 m", "NOAA NCEI WOA22", "10²² J",
          [("steps", ohc_pts, ohc_band)], (1955, end))

    # ── binary tile band (bottom 100 mm) ──
    payload = serialize(S)
    per_copy = math.ceil(len(payload) / PAYLOAD_BYTES)
    n_tiles = BAND_COLS * BAND_ROWS
    starts = [0, n_tiles // 3, 2 * n_tiles // 3]        # physically separated copies
    chunk_at = {}
    for s in starts:
        for k in range(per_copy):
            chunk_at[s + k] = payload[k * PAYLOAD_BYTES:(k + 1) * PAYLOAD_BYTES]
    band_y = ACTIVE[1] - 100
    band_w, band_h = BAND_COLS * TILE_PITCH * PITCH, BAND_ROWS * TILE_PITCH * PITCH
    bx0, by0 = (ACTIVE[0] - band_w) / 2, band_y + (100 - band_h) / 2
    dot = max(1, round(0.11 * p.ppm))
    for idx in range(n_tiles):
        tr, tc = divmod(idx, BAND_COLS)
        g = tile_modules(1, tr, tc, chunk_at.get(idx))
        ox, oy = bx0 + (tc * TILE_PITCH + 1) * PITCH, by0 + (tr * TILE_PITCH + 1) * PITCH
        for r in range(TILE):
            row = g[r]
            for c in range(TILE):
                if row[c]:
                    # tile row 0 is the top row; finder bottom row is r = 63
                    X0, Y0 = p.px(ox + c * PITCH, oy + r * PITCH)
                    p.d.rectangle([X0, Y0, X0 + dot - 1, Y0 + dot - 1], fill=MARK)

    p.img.save(args.out, optimize=True)
    small = p.img.resize((2000, 2000), Image.LANCZOS)
    small_path = Path(args.out).with_name(Path(args.out).stem + "_2000px.png")
    small.save(small_path, optimize=True)
    print(f"payload {len(payload)} bytes -> {per_copy} tiles per copy x 3 = {3 * per_copy} of {n_tiles} tiles")
    print(f"wrote {args.out} ({p.img.width}x{p.img.height}) and {small_path}")


if __name__ == "__main__":
    main()
