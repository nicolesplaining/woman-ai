"""Personalization: filament palette, motif library, custom SVG motifs, and the gauntlet pattern height map."""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FILAMENTS = [
    {"id": "white", "name": "White", "hex": "#F4F4F2"},
    {"id": "black", "name": "Black", "hex": "#1E1E1E"},
    {"id": "gray", "name": "Gray", "hex": "#8A8D91"},
    {"id": "silver", "name": "Silver", "hex": "#C7CBD1"},
    {"id": "red", "name": "Red", "hex": "#D7263D"},
    {"id": "orange", "name": "Orange", "hex": "#F46036"},
    {"id": "yellow", "name": "Yellow", "hex": "#F9C80E"},
    {"id": "lime", "name": "Lime green", "hex": "#9BE564"},
    {"id": "green", "name": "Green", "hex": "#2E933C"},
    {"id": "teal", "name": "Teal", "hex": "#1B998B"},
    {"id": "sky", "name": "Sky blue", "hex": "#5BC0EB"},
    {"id": "blue", "name": "Blue", "hex": "#2364AA"},
    {"id": "navy", "name": "Navy", "hex": "#1D3461"},
    {"id": "purple", "name": "Purple", "hex": "#7B2CBF"},
    {"id": "lavender", "name": "Lavender", "hex": "#B79CED"},
    {"id": "pink", "name": "Pink", "hex": "#FF8FAB"},
    {"id": "magenta", "name": "Magenta", "hex": "#D81E5B"},
    {"id": "brown", "name": "Brown", "hex": "#7F5539"},
    {"id": "tan", "name": "Tan", "hex": "#D4A373"},
    {"id": "gold", "name": "Gold (silk)", "hex": "#D4AF37"},
    {"id": "glow", "name": "Glow in the dark", "hex": "#C9F2C7"},
]
FILAMENT_IDS = [f["id"] for f in FILAMENTS]
FILAMENT_BY_ID = {f["id"]: f for f in FILAMENTS}

MOTIFS = {
    "none": "No pattern",
    "stars": "Five-point stars",
    "hearts": "Hearts",
    "lightning": "Lightning bolts",
    "snowflakes": "Snowflakes",
    "paws": "Paw prints",
    "scales": "Dragon/fish scales",
    "dots": "Polka dots",
    "flowers": "Flowers",
    "hexagons": "Honeycomb",
    "waves": "Waves",
    "webs": "Web lines",
}

MIN_FEATURE_MM = 1.0


def _font(size_px: int) -> ImageFont.FreeTypeFont:
    for p in [
        "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if Path(p).exists():
            return ImageFont.truetype(p, size_px)
    return ImageFont.load_default(size=size_px)


def _star(d: ImageDraw.ImageDraw, cx, cy, r):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=255)


def _heart(d, cx, cy, r):
    pts = []
    for i in range(60):
        t = 2 * math.pi * i / 60
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * r / 17, cy + y * r / 17))
    d.polygon(pts, fill=255)


def _lightning(d, cx, cy, r):
    pts = [(0.15, -1), (-0.55, 0.12), (-0.05, 0.12), (-0.25, 1), (0.55, -0.18), (0.05, -0.18), (0.35, -1)]
    d.polygon([(cx + x * r * 0.8, cy + y * r) for x, y in pts], fill=255)


def _snowflake(d, cx, cy, r, w):
    for k in range(6):
        a = k * math.pi / 3
        ex, ey = cx + r * math.cos(a), cy + r * math.sin(a)
        d.line([(cx, cy), (ex, ey)], fill=255, width=w)
        for f in (0.55,):
            bx, by = cx + r * f * math.cos(a), cy + r * f * math.sin(a)
            for s in (-1, 1):
                b = a + s * math.pi / 4
                d.line([(bx, by), (bx + r * 0.32 * math.cos(b), by + r * 0.32 * math.sin(b))], fill=255, width=w)


def _paw(d, cx, cy, r):
    d.ellipse([cx - r * 0.5, cy - r * 0.05, cx + r * 0.5, cy + r * 0.75], fill=255)
    for x, y in [(-0.62, -0.25), (-0.22, -0.62), (0.22, -0.62), (0.62, -0.25)]:
        d.ellipse([cx + x * r - r * 0.2, cy + y * r - r * 0.24, cx + x * r + r * 0.2, cy + y * r + r * 0.24], fill=255)


def _flower(d, cx, cy, r):
    for k in range(5):
        a = k * 2 * math.pi / 5
        px, py = cx + r * 0.5 * math.cos(a), cy + r * 0.5 * math.sin(a)
        d.ellipse([px - r * 0.42, py - r * 0.42, px + r * 0.42, py + r * 0.42], fill=255)
    d.ellipse([cx - r * 0.28, cy - r * 0.28, cx + r * 0.28, cy + r * 0.28], fill=0)
    d.ellipse([cx - r * 0.16, cy - r * 0.16, cx + r * 0.16, cy + r * 0.16], fill=255)


def _svg_polys(svg_text: str, size_px: float) -> list[list[tuple[float, float]]]:
    """Parse a simple SVG and return filled polygons scaled to fit a size_px box centered on (0, 0)."""
    from svgelements import SVG, Path, Shape

    svg = SVG.parse(io.StringIO(svg_text))
    polys = []
    for el in svg.elements():
        if not isinstance(el, Shape):
            continue
        try:
            for sub in Path(el).as_subpaths():
                sp = Path(sub)
                if sp.length() <= 0:
                    continue
                pts = [(q.x, q.y) for q in (sp.point(t) for t in np.linspace(0, 1, 120))]
                if len(pts) >= 3:
                    polys.append(pts)
        except Exception:
            continue
        if len(polys) > 40:
            break
    if not polys:
        raise ValueError("SVG had no drawable shapes")
    allp = np.array([p for poly in polys for p in poly])
    lo, hi = allp.min(0), allp.max(0)
    sc = size_px / max(float((hi - lo).max()), 1e-6)
    mid = (lo + hi) / 2
    return [[((x - mid[0]) * sc, (y - mid[1]) * sc) for x, y in poly] for poly in polys]


def motif_tile(motif: str, size_mm: float, spacing_mm: float, res: float, custom_svg: Optional[str] = None) -> np.ndarray:
    """One repeat cell of the motif as a 0..1 image (rows = u, cols = v)."""
    cell = max(size_mm + spacing_mm, 4.0)
    n = max(int(round(cell / res)), 8)
    img = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(img)
    c = n / 2
    r = size_mm / 2 / res
    w = max(int(round(1.4 / res)), 2)
    if motif == "stars":
        _star(d, c, c, r)
    elif motif == "hearts":
        _heart(d, c, c, r)
    elif motif == "lightning":
        _lightning(d, c, c, r)
    elif motif == "snowflakes":
        _snowflake(d, c, c, r, w)
    elif motif == "paws":
        _paw(d, c, c, r)
    elif motif == "dots":
        d.ellipse([c - r * 0.7, c - r * 0.7, c + r * 0.7, c + r * 0.7], fill=255)
    elif motif == "flowers":
        _flower(d, c, c, r)
    elif motif == "custom" and custom_svg:
        for poly in _svg_polys(custom_svg, 2 * r):
            d.polygon([(c + x, c + y) for x, y in poly], fill=255)
    return np.asarray(img, np.float32) / 255.0


def _continuous(motif: str, H: np.ndarray, uu: np.ndarray, vv: np.ndarray, size_mm: float, spacing_mm: float, res: float):
    w = max(1.4, size_mm * 0.12)
    if motif == "scales":
        rr = size_mm / 2
        pitch_u, pitch_v = size_mm, size_mm * 0.7
        img = np.zeros_like(H)
        row = np.floor(vv / pitch_v)
        uo = uu + (row % 2) * pitch_u / 2
        cu = (np.floor(uo / pitch_u) + 0.5) * pitch_u
        cv = (row + 1.0) * pitch_v
        dist = np.hypot(uo - cu, vv - cv)
        img = ((np.abs(dist - rr) < w / 2) & (vv < cv)).astype(np.float32)
        return img
    if motif == "hexagons":
        a = size_mm / 2
        x, y = vv / (1.5 * a), uu / (math.sqrt(3) * a)
        q = np.round(x)
        r_ = np.round(y - (q % 2) * 0.5)
        cx = q * 1.5 * a
        cy = (r_ + (q % 2) * 0.5) * math.sqrt(3) * a
        dx, dy = np.abs(vv - cx), np.abs(uu - cy)
        hexd = np.maximum(dx * 0.5 + dy * math.sqrt(3) / 2, dx)
        return (np.abs(hexd - a * 0.86) < w / 2).astype(np.float32)
    if motif == "waves":
        period = size_mm * 1.6
        amp = size_mm * 0.25
        band = size_mm * 0.9
        phase = uu - amp * np.sin(2 * np.pi * vv / period)
        return (np.abs((phase % band) - band / 2) < w / 2).astype(np.float32)
    if motif == "webs":
        spoke = size_mm * 1.2
        ring = size_mm * 1.6
        a = (np.abs(((uu + vv) % spoke) - spoke / 2) < w / 2) | (np.abs(((uu - vv) % spoke) - spoke / 2) < w / 2)
        b = np.abs((vv % ring) - ring / 2) < w / 2
        return (a | b).astype(np.float32)
    return None


def _clean(img: np.ndarray, res: float) -> np.ndarray:
    """Remove features thinner than MIN_FEATURE_MM and soften edges slightly."""
    k = max(int(round(MIN_FEATURE_MM / res)), 1)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    b = (img > 0.5).astype(np.uint8)
    b = cv2.morphologyEx(b, cv2.MORPH_OPEN, kern)
    out = cv2.GaussianBlur(b.astype(np.float32), (0, 0), 0.3 / res)
    return np.clip(out, 0, 1)


def pattern_builder(spec: dict):
    """Return a function (u_min, u_max, v_min, v_max, res) -> height fraction map (0..1)."""
    motif = spec.get("motif", "none")
    size_mm = float(np.clip(spec.get("size_mm", 14), 6, 30))
    spacing_mm = float(np.clip(spec.get("spacing_mm", 8), 3, 30))
    text = (spec.get("name_text") or "").strip()[:14]
    custom_svg = spec.get("custom_svg")

    def build(u_min, u_max, v_min, v_max, res):
        nu = int(np.ceil((u_max - u_min) / res))
        nv = int(np.ceil((v_max - v_min) / res))
        uu = (np.arange(nu) * res + u_min)[:, None] * np.ones((1, nv))
        vv = (np.arange(nv) * res + v_min)[None, :] * np.ones((nu, 1))
        H = np.zeros((nu, nv), np.float32)
        cont = _continuous(motif, H, uu, vv, size_mm, spacing_mm, res) if motif in ("scales", "hexagons", "waves", "webs") else None
        if cont is not None:
            H = cont
        elif motif not in ("none", None):
            tile = motif_tile(motif, size_mm, spacing_mm, res, custom_svg)
            th, tw = tile.shape
            # brick layout: every other column of tiles shifted by half a tile in u
            reps_u = nu // th + 3
            reps_v = nv // tw + 3
            col = np.tile(tile, (reps_u, 1))
            big = np.zeros((reps_u * th, reps_v * tw), np.float32)
            for j in range(reps_v):
                shift = (th // 2) if j % 2 else 0
                big[:, j * tw : (j + 1) * tw] = np.roll(col, shift, axis=0)
            ou = int(((-u_min) % th))
            H = big[ou : ou + nu, :nv]
        if text:
            # name on the +x side, reading along the forearm (wrist -> elbow)
            h_px = int(round(11 / res))
            font = _font(h_px)
            bbox = font.getbbox(text)
            tw_px, th_px = bbox[2] - bbox[0] + 8, bbox[3] - bbox[1] + 8
            timg = Image.new("L", (tw_px, th_px), 0)
            ImageDraw.Draw(timg).text((4 - bbox[0], 4 - bbox[1]), text, fill=255, font=font)
            max_w = int(0.8 * nv)
            if tw_px > max_w:
                timg = timg.resize((max_w, int(th_px * max_w / tw_px)))
            t = np.flipud(np.asarray(timg, np.float32) / 255.0)  # rows increase with u (= up on the surface)
            u_center = spec.get("_name_u", 0.17 * (u_max - u_min))  # +x side, between the track and the strap slots
            r0 = int((u_center - u_min) / res - t.shape[0] / 2)
            c0 = int(nv / 2 - t.shape[1] / 2)
            r0 = int(np.clip(r0, 0, nu - t.shape[0]))
            c0 = int(np.clip(c0, 0, nv - t.shape[1]))
            clear = int(3 / res)
            H[max(r0 - clear, 0) : r0 + t.shape[0] + clear, max(c0 - clear, 0) : c0 + t.shape[1] + clear] = 0
            H[r0 : r0 + t.shape[0], c0 : c0 + t.shape[1]] = np.maximum(H[r0 : r0 + t.shape[0], c0 : c0 + t.shape[1]], t)
        return _clean(H, res)

    return build


def preview_swatch(spec: dict, size=(360, 180)) -> bytes:
    """Flat preview of the pattern in the gauntlet color, for the UI and Claude's review."""
    res = 0.25
    w_mm, h_mm = size[0] * res, size[1] * res
    H = pattern_builder(spec)(-h_mm / 2, h_mm / 2, 0, w_mm, res)
    base = np.array(list(bytes.fromhex(FILAMENT_BY_ID.get(spec.get("gauntlet_color", "purple"), FILAMENTS[13])["hex"][1:])), float)
    img = base[None, None, :] * (0.78 + 0.22 * np.flipud(H)[..., None])
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()
