"""Photo mode: printable marker sheet, marker detection + perspective rectification, arm
segmentation, and width profiles along the arm."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .design import _font

# Sheet layout in mm. Origin = top-left corner of the top-left marker; X right, Y down.
SHEET_W, SHEET_H, MARKER = 190.0, 255.0, 30.0
MARKER_POS = {0: (0.0, 0.0), 1: (SHEET_W - MARKER, 0.0), 2: (SHEET_W - MARKER, SHEET_H - MARKER), 3: (0.0, SHEET_H - MARKER)}
ARUCO = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
PAPER = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}

PPM = 3.0  # rectified pixels per mm
MARGIN = 30.0  # mm of context kept around the sheet in the rectified image
VALID_PAD = 4.0  # mm past the marker rectangle that is certainly paper


class PhotoError(Exception):
    def __init__(self, message: str, retake: str):
        super().__init__(message)
        self.retake = retake


# ---------------------------------------------------------------- marker sheet

def marker_sheet(paper: str = "letter", dpi: int = 300) -> Image.Image:
    pw, ph = PAPER.get(paper, PAPER["letter"])
    px = dpi / 25.4
    img = Image.new("RGB", (round(pw * px), round(ph * px)), "white")
    d = ImageDraw.Draw(img)
    ox, oy = (pw - SHEET_W) / 2, (ph - SHEET_H) / 2

    def P(x, y):
        return (round((ox + x) * px), round((oy + y) * px))

    for mid, (mx, my) in MARKER_POS.items():
        side = round(MARKER * px)
        m = cv2.aruco.generateImageMarker(ARUCO, mid, side, borderBits=1)
        img.paste(Image.fromarray(m).convert("RGB"), P(mx, my))
    f_big, f_small = _font(round(5 * px)), _font(round(3 * px))
    d.text(P(SHEET_W / 2, 9), "HAND THIS WAY  ^", fill=(90, 90, 90), font=f_big, anchor="mm")
    d.text(P(SHEET_W / 2, 18), "wrist crease about here", fill=(150, 150, 150), font=f_small, anchor="mm")
    # 100 mm scale check between the bottom markers
    y = SHEET_H - 12
    x0, x1 = SHEET_W / 2 - 50, SHEET_W / 2 + 50
    d.line([P(x0, y), P(x1, y)], fill=(60, 60, 60), width=max(1, round(0.4 * px)))
    for i in range(11):
        xx = x0 + i * 10
        d.line([P(xx, y - (3 if i % 5 == 0 else 1.5)), P(xx, y)], fill=(60, 60, 60), width=max(1, round(0.3 * px)))
    d.text(P(SHEET_W / 2, y + 5), "Print at 100% - this line should measure 100 mm", fill=(110, 110, 110), font=f_small, anchor="mm")
    return img


def marker_sheet_pdf(paper: str = "letter") -> bytes:
    buf = io.BytesIO()
    marker_sheet(paper).save(buf, "PDF", resolution=300)
    return buf.getvalue()


def marker_sheet_png(paper: str = "letter", dpi: int = 150) -> bytes:
    buf = io.BytesIO()
    marker_sheet(paper, dpi).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- loading + rectification

def load_photo(data: bytes, max_side: int = 2400) -> tuple[np.ndarray, Optional[float]]:
    """Returns (RGB array, 35mm-equivalent focal length from EXIF if present)."""
    im = Image.open(io.BytesIO(data))
    f35 = None
    try:
        exif = im.getexif()
        sub = exif.get_ifd(0x8769)
        f35 = sub.get(41989) or exif.get(41989)
        f35 = float(f35) if f35 else None
    except Exception:
        f35 = None
    im = ImageOps.exif_transpose(im).convert("RGB")
    s = max_side / max(im.size)
    if s < 1:
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    return np.asarray(im), f35


@dataclass
class Rectified:
    image: np.ndarray  # RGB, sheet space, PPM px/mm, offset by MARGIN
    H: np.ndarray  # original image px -> rectified px
    n_markers: int
    camera_height_mm: float
    focal_px: float
    blur: float
    reproj_mm: float
    warnings: list[str] = field(default_factory=list)

    def to_px(self, xy_mm: np.ndarray) -> np.ndarray:
        return (np.asarray(xy_mm) + MARGIN) * PPM

    def to_mm(self, xy_px: np.ndarray) -> np.ndarray:
        return np.asarray(xy_px) / PPM - MARGIN


def _camera_height(H_sheet_to_img: np.ndarray, f_px: float, cx: float, cy: float) -> float:
    K = np.array([[f_px, 0, cx], [0, f_px, cy], [0, 0, 1.0]])
    h = np.linalg.inv(K) @ H_sheet_to_img
    lam = 2.0 / (np.linalg.norm(h[:, 0]) + np.linalg.norm(h[:, 1]))
    r1, r2, t = lam * h[:, 0], lam * h[:, 1], lam * h[:, 2]
    if t[2] < 0:
        r1, r2, t = -r1, -r2, -t
    R = np.stack([r1, r2, np.cross(r1, r2)], axis=1)
    C = -R.T @ t
    return float(abs(C[2]))


def rectify(rgb: np.ndarray, f35: Optional[float]) -> Rectified:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(ARUCO, params)
    corners, ids, _ = det.detectMarkers(gray)
    found = {} if ids is None else {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners) if int(i) in MARKER_POS}
    if len(found) < 2:
        raise PhotoError(
            f"Found {len(found)} of the 4 corner markers.",
            "Make sure all four black corner squares on the printed sheet are in the photo, not covered, and in focus.",
        )
    src, dst = [], []
    for mid, c in found.items():
        mx, my = MARKER_POS[mid]
        src.append(c)
        dst.append(np.array([[mx, my], [mx + MARKER, my], [mx + MARKER, my + MARKER], [mx, my + MARKER]]))
    src = np.vstack(src).astype(np.float64)
    dst_mm = np.vstack(dst).astype(np.float64)
    H_img_to_mm, _ = cv2.findHomography(src, dst_mm, cv2.RANSAC, 2.0)
    if H_img_to_mm is None:
        raise PhotoError("Couldn't work out the sheet's position.", "Retake the photo from directly above the sheet.")
    proj = cv2.perspectiveTransform(src[None], H_img_to_mm)[0]
    reproj = float(np.linalg.norm(proj - dst_mm, axis=1).mean())

    T = np.array([[PPM, 0, MARGIN * PPM], [0, PPM, MARGIN * PPM], [0, 0, 1.0]])
    H = T @ H_img_to_mm
    out_w = round((SHEET_W + 2 * MARGIN) * PPM)
    out_h = round((SHEET_H + 2 * MARGIN) * PPM)
    rect = cv2.warpPerspective(rgb, H, (out_w, out_h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))

    h_img, w_img = gray.shape
    diag = float(np.hypot(w_img, h_img))
    f_px = (f35 or 26.0) * diag / 43.27
    cam_h = _camera_height(np.linalg.inv(H_img_to_mm), f_px, w_img / 2, h_img / 2)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    warnings = []
    if len(found) < 4:
        warnings.append(f"Only {len(found)} of 4 markers were visible; measurements may be less accurate.")
    if f35 is None:
        warnings.append("The photo has no lens information, so perspective correction assumes a standard 1x phone lens.")
    if cam_h < 350:
        warnings.append(f"The camera was only about {cam_h / 10:.0f} cm above the sheet; shoot from about 1 m with 2x zoom for better accuracy.")
    return Rectified(rect, H, len(found), cam_h, f_px, blur, reproj, warnings)


def valid_mask(shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    m = np.zeros((h, w), bool)
    x0, y0 = (np.array([-VALID_PAD, -VALID_PAD]) + MARGIN) * PPM
    x1, y1 = (np.array([SHEET_W + VALID_PAD, SHEET_H + VALID_PAD]) + MARGIN) * PPM
    m[int(y0) : int(y1), int(x0) : int(x1)] = True
    for mx, my in MARKER_POS.values():
        a = ((np.array([mx, my]) - 2 + MARGIN) * PPM).astype(int)
        b = ((np.array([mx + MARKER, my + MARKER]) + 2 + MARGIN) * PPM).astype(int)
        m[a[1] : b[1], a[0] : b[0]] = False
    return m


# ---------------------------------------------------------------- segmentation

def segment_arm(rect: Rectified) -> np.ndarray:
    img = rect.image
    valid = valid_mask(img.shape[:2])
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float32)
    L, a, b = lab[..., 0], lab[..., 1] - 128, lab[..., 2] - 128
    Lv = np.where(valid, L, 0).astype(np.float32)
    k = int(90 * PPM) | 1
    bg = cv2.dilate(Lv, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), 15 * PPM)
    ratio = L / np.maximum(bg, 1)
    paper = valid & (ratio > 0.93)
    if paper.sum() < 1000:
        raise PhotoError("Couldn't find the white paper.", "Use a well-lit spot and keep the sheet flat and fully in frame.")
    a0, b0 = np.median(a[paper]), np.median(b[paper])
    chroma = np.hypot(a - a0, b - b0)

    # Skin has color; shadows are gray and only moderately darker. Very dark pixels count as arm.
    sure_fg = valid & ((chroma > 12) | (ratio < 0.35))
    prob_fg = valid & ((chroma > 7) | (ratio < 0.45))
    sure_bg = (~valid) | ((chroma < 3.5) & (ratio > 0.9))
    shadow = valid & (chroma < 5) & (ratio >= 0.45) & ~sure_bg
    sure_fg = cv2.erode(sure_fg.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    if sure_fg.sum() < 2000:
        raise PhotoError("Couldn't find the arm on the sheet.", "Lay the forearm flat along the middle of the sheet, sleeve rolled up, with the wrist near the top.")

    mask = np.full(img.shape[:2], cv2.GC_PR_BGD, np.uint8)
    mask[prob_fg] = cv2.GC_PR_FGD
    mask[shadow] = cv2.GC_PR_BGD
    mask[sure_bg] = cv2.GC_BGD
    mask[sure_fg] = cv2.GC_FGD
    bgd, fgd = np.zeros((1, 65)), np.zeros((1, 65))
    try:
        cv2.grabCut(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), mask, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        pass
    fg = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)) & valid
    fg = fg.astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(3 * PPM),) * 2))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(5 * PPM),) * 2))
    n, lab_img, stats, _ = cv2.connectedComponentsWithStats(fg)
    if n <= 1:
        raise PhotoError("Couldn't find the arm on the sheet.", "Lay the forearm flat along the middle of the sheet.")
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    fg = (lab_img == biggest).astype(np.uint8)
    # fill holes
    inv = 1 - fg
    n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(inv)
    for i in range(1, n2):
        x, y, w, h, area = stats2[i]
        if x > 0 and y > 0 and x + w < fg.shape[1] and y + h < fg.shape[0]:
            fg[lab2 == i] = 1
    return fg.astype(bool)


# ---------------------------------------------------------------- profiles

@dataclass
class Profile:
    s: np.ndarray  # mm along the axis from the distal end of the visible arm, increasing toward the elbow
    width: np.ndarray
    center_t: np.ndarray  # signed offset of the run center from the axis line, mm
    valid: np.ndarray
    origin: np.ndarray  # mm (sheet coords) of s = 0 on the axis
    axis: np.ndarray  # unit vector toward the elbow (sheet coords)
    normal: np.ndarray  # unit vector perpendicular to the axis

    def at(self, s_query: float) -> float:
        return float(np.interp(s_query, self.s, self.width))


def width_profile(mask: np.ndarray, hand_end: str = "top", step: float = 1.0) -> Profile:
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], axis=1) / PPM - MARGIN  # mm
    c = pts.mean(axis=0)
    cov = np.cov((pts - c).T)
    evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, np.argmax(evals)]
    # toward the elbow = away from the hand end
    if (hand_end == "top" and axis[1] < 0) or (hand_end == "bottom" and axis[1] > 0):
        axis = -axis
    normal = np.array([-axis[1], axis[0]])
    proj = (pts - c) @ axis
    origin = c + axis * proj.min()
    length = float(proj.max() - proj.min())
    s = np.arange(0, length, step)
    t = np.arange(-110, 110, 0.25)
    vm = valid_mask(mask.shape)
    h, w = mask.shape
    widths = np.zeros_like(s)
    centers = np.zeros_like(s)
    valid = np.zeros_like(s, bool)
    prev_c = 0.0
    for i, si in enumerate(s):
        line = origin + axis * si + normal * t[:, None]
        px = np.round((line + MARGIN) * PPM).astype(int)
        inside = (px[:, 0] >= 0) & (px[:, 0] < w) & (px[:, 1] >= 0) & (px[:, 1] < h)
        vals = np.zeros(len(t), bool)
        vv = np.zeros(len(t), bool)
        vals[inside] = mask[px[inside, 1], px[inside, 0]]
        vv[inside] = vm[px[inside, 1], px[inside, 0]]
        if not vals.any():
            continue
        d = np.diff(np.concatenate([[0], vals.astype(int), [0]]))
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        mids = (t[starts] + t[ends - 1]) / 2
        j = int(np.argmin(np.abs(mids - prev_c)))
        a_, b_ = starts[j], ends[j] - 1
        widths[i] = (b_ - a_ + 1) * 0.25
        centers[i] = mids[j]
        prev_c = mids[j]
        lo, hi = max(a_ - 8, 0), min(b_ + 8, len(t) - 1)
        valid[i] = bool(vv[lo] and vv[hi])
    return Profile(s, widths, centers, valid, origin, axis, normal)


def smooth_profile(p: Profile, window_mm: float = 5.0) -> np.ndarray:
    k = max(int(window_mm), 1)
    w = p.width.copy()
    w[~p.valid] = np.nan
    from scipy.ndimage import median_filter, uniform_filter1d

    filled = np.where(np.isnan(w), np.interp(np.arange(len(w)), np.nonzero(~np.isnan(w))[0], w[~np.isnan(w)]) if (~np.isnan(w)).any() else 0, w)
    return uniform_filter1d(median_filter(filled, size=k), size=k)


def refine_wrist(p: Profile, estimate: Optional[float], hand_present: bool = True) -> tuple[float, str]:
    """Snap a wrist estimate (s, mm) to the narrowest point nearby; fall back to a heuristic."""
    w = smooth_profile(p)
    ok = p.valid & (p.width > 0)
    if estimate is not None and 0 <= estimate <= p.s[-1]:
        win = ok & (np.abs(p.s - estimate) <= 22)
        if win.sum() >= 5:
            i = np.nonzero(win)[0][np.argmin(w[win])]
            return float(p.s[i]), "photo check + narrowest point"
        return float(estimate), "photo check"
    # heuristic: widest point in the distal 45% (hand), then the narrowest point within 100 mm past it
    n = len(p.s)
    distal = ok & (np.arange(n) < 0.45 * n)
    if distal.sum() < 5:
        raise PhotoError("Couldn't find the wrist.", "Put the wrist crease on the sheet, near the top edge.")
    i_hand = np.nonzero(distal)[0][np.argmax(w[distal])]
    win = ok & (p.s > p.s[i_hand] + 5) & (p.s < p.s[i_hand] + 100)
    if win.sum() < 5:
        raise PhotoError("Couldn't find the wrist.", "Put the wrist crease on the sheet, near the top edge.")
    i = np.nonzero(win)[0][np.argmin(w[win])]
    return float(p.s[i]), "narrowest point"


# ---------------------------------------------------------------- overlays for Claude and the UI

def overlay(rect: Rectified, mask: np.ndarray, prof: Profile, title: str, wrist_s: Optional[float] = None) -> bytes:
    img = rect.image.copy()
    green = np.zeros_like(img)
    green[..., 1] = 255
    edge = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    img[mask] = (img[mask] * 0.82 + green[mask] * 0.18).astype(np.uint8)
    img[edge] = (0, 200, 0)
    pil = Image.fromarray(img)
    d = ImageDraw.Draw(pil)
    f = _font(15)
    p0 = rect.to_px(prof.origin)
    p1 = rect.to_px(prof.origin + prof.axis * prof.s[-1])
    d.line([tuple(p0), tuple(p1)], fill=(30, 90, 230), width=2)
    for si in np.arange(0, prof.s[-1], 10):
        c = prof.origin + prof.axis * si
        half = 6 if int(si) % 20 == 0 else 3
        a = rect.to_px(c - prof.normal * half)
        b = rect.to_px(c + prof.normal * half)
        d.line([tuple(a), tuple(b)], fill=(30, 90, 230), width=2)
        if int(si) % 20 == 0:
            lp = rect.to_px(c + prof.normal * 60)
            d.text(tuple(lp), f"{int(si)}", fill=(20, 60, 200), font=f, anchor="mm", stroke_width=3, stroke_fill="white")
    if wrist_s is not None:
        c = prof.origin + prof.axis * wrist_s
        a = rect.to_px(c - prof.normal * 50)
        b = rect.to_px(c + prof.normal * 50)
        d.line([tuple(a), tuple(b)], fill=(230, 40, 90), width=3)
    d.rectangle([0, 0, pil.width, 26], fill=(255, 255, 255))
    d.text((8, 4), title, fill=(0, 0, 0), font=_font(17))
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=88)
    return buf.getvalue()
