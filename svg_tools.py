"""Minimal millimetre-based SVG canvas for laser files (Bambu Studio).

- Document units are mm (width/height in mm, viewBox in mm), so 1 user unit = 1 mm.
- Text is converted to filled outline paths with fontTools, so the file does not
  depend on fonts being installed wherever it is opened.
- Content is grouped by intended laser process: "fill" (areas: text, dots,
  solid shapes) and "line" (strokes: chart lines, axes, frames). Bambu Studio
  may need these set per object after import.
"""
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


class _Font:
    def __init__(self, path):
        self.tt = TTFont(path)
        self.cmap = self.tt.getBestCmap()
        self.glyphs = self.tt.getGlyphSet()
        self.upem = self.tt["head"].unitsPerEm
        self.asc = self.tt["hhea"].ascent
        self.desc = self.tt["hhea"].descent  # negative

    def width(self, text, size):
        return sum(self.glyphs[self.cmap.get(ord(ch), ".notdef")].width for ch in text) * size / self.upem

    def path(self, text, x, baseline, size):
        """SVG path data for text with its baseline at (x, baseline); size = em in mm."""
        k = size / self.upem
        pen = SVGPathPen(self.glyphs, ntos=lambda v: f"{v:.3f}".rstrip("0").rstrip("."))
        cx = x
        for ch in text:
            g = self.glyphs[self.cmap.get(ord(ch), ".notdef")]
            # font units are y-up; SVG is y-down
            g.draw(TransformPen(pen, (k, 0, 0, -k, cx, baseline)))
            cx += g.width * k
        return pen.getCommands()


class SvgCanvas:
    def __init__(self, width_mm, height_mm, title=""):
        self.w, self.h, self.title = width_mm, height_mm, title
        self.groups = {"line": [], "fill": []}
        self._fonts = {}

    def font(self, bold=False):
        p = FONT_B if bold else FONT
        if p not in self._fonts:
            self._fonts[p] = _Font(p)
        return self._fonts[p]

    # anchors follow Pillow: first letter l/m/r = left/middle/right,
    # second a/m/s = top (ascender) / middle / baseline
    def text(self, x, y, s, size, bold=False, anchor="la"):
        f = self.font(bold)
        w = f.width(s, size)
        x0 = x - {"l": 0, "m": w / 2, "r": w}[anchor[0]]
        k = size / f.upem
        base = {"a": y + f.asc * k, "m": y + (f.asc + f.desc) / 2 * k, "s": y}[anchor[1]]
        d = f.path(s, x0, base, size)
        if d:
            self.groups["fill"].append(f'<path d="{d}"/>')

    def line(self, pts, width=0.25):
        d = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts)
        self.groups["line"].append(f'<path d="{d}" stroke-width="{width:g}"/>')

    def rect(self, x0, y0, x1, y1, width=0.2):
        self.groups["line"].append(
            f'<rect x="{x0:.3f}" y="{y0:.3f}" width="{x1 - x0:.3f}" height="{y1 - y0:.3f}" stroke-width="{width:g}"/>')

    def fill_rect(self, x0, y0, x1, y1):
        self.groups["fill"].append(
            f'<rect x="{x0:.4f}" y="{y0:.4f}" width="{x1 - x0:.4f}" height="{y1 - y0:.4f}"/>')

    def dot(self, x, y, r):
        self.groups["fill"].append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{r:g}"/>')

    def squares(self, centers, size):
        """Many small filled squares (e.g. tile modules) as ONE path element."""
        h = size / 2
        d = "".join(f"M{x - h:.4f},{y - h:.4f}h{size:g}v{size:g}h-{size:g}z" for x, y in centers)
        if d:
            self.groups["fill"].append(f'<path d="{d}"/>')

    def save(self, path):
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}mm" height="{self.h}mm" '
            f'viewBox="0 0 {self.w} {self.h}">',
            f"<title>{self.title}</title>",
            '<g id="line" fill="none" stroke="#000" stroke-linecap="round" stroke-linejoin="round">',
            *self.groups["line"], "</g>",
            '<g id="fill" fill="#000" stroke="none">',
            *self.groups["fill"], "</g>",
            "</svg>",
        ]
        with open(path, "w") as f:
            f.write("\n".join(out) + "\n")
