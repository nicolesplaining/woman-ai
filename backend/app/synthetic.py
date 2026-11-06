"""Synthetic arms and photos for demos and accuracy tests (no real patient data).

Base dimensions follow ANSUR II female medians (hand length 180 mm, hand breadth 78 mm,
wrist circumference ~154 mm); arm_model(k=0.8) scales them to roughly a 10-year-old."""
from __future__ import annotations

import io

import cv2
import numpy as np
import trimesh
from PIL import Image

from .limb import LimbModel, superellipse_contour
from .photo import MARGIN, PAPER, SHEET_H, SHEET_W, marker_sheet
from .render import Layer, render_perspective

FOREARM = {
    "z": [0, 25, 60, 110, 160, 200, 250],
    "w": [56, 58, 63, 70, 74, 74, 72],
    "d": [37, 40, 46, 54, 60, 62, 63],
}
# Partial hand: palm present, fingers absent (transmetacarpal-like residual).
RESIDUAL = {"z": [-70, -66, -58, -45, -28, -12, 0], "w": [12, 44, 66, 72, 70, 62, 56], "d": [8, 16, 24, 28, 31, 34, 37]}
# Full hand, fingers together, thumb against the side. Tip at z = -180 (hand length 180).
SOUND = {
    "z": [-180, -177, -170, -150, -122, -106, -90, -60, -35, -15, 0],
    "w": [10, 36, 54, 66, 72, 78, 80, 80, 74, 62, 56],
    "d": [6, 12, 16, 20, 23, 26, 28, 30, 31, 34, 37],
}


def arm_model(hand: dict, n: int = 160, step: float = 1.0, k: float = 1.0) -> LimbModel:
    """k scales every dimension (0.8 is roughly a 10-year-old)."""
    zs = np.concatenate([hand["z"][:-1], FOREARM["z"]]) * k
    ws = np.concatenate([hand["w"][:-1], FOREARM["w"]]) * k
    ds = np.concatenate([hand["d"][:-1], FOREARM["d"]]) * k
    z = np.arange(zs[0], zs[-1] + 1e-6, step)
    from scipy.interpolate import PchipInterpolator

    W = PchipInterpolator(zs, ws)(z)
    D = PchipInterpolator(zs, ds)(z)
    C = np.stack([superellipse_contour(0, 0, a / 2, b / 2, n, 2.3) for a, b in zip(W, D)])
    return LimbModel(z=z, contours=C, source="synthetic", uncertainty_mm=0.0)


def _place(mesh: trimesh.Trimesh, view: str, wrist_xy: tuple[float, float]) -> trimesh.Trimesh:
    """Arm frame -> world (sheet X right, Y down, Z into the table). Rests on the table."""
    v = mesh.vertices
    if view == "top":  # palm down
        X, Y, Zup = v[:, 0], v[:, 2], v[:, 1]
    else:  # on its side, thumb up
        X, Y, Zup = -v[:, 1], v[:, 2], v[:, 0]
    # rest each slice on the table
    zi = np.round(v[:, 2]).astype(int)
    lo = {}
    for k, h in zip(zi, Zup):
        lo[k] = min(lo.get(k, 1e9), h)
    base = np.array([lo[k] for k in zi])
    world = np.stack([X + wrist_xy[0], Y + wrist_xy[1], -(Zup - base)], axis=1)
    return trimesh.Trimesh(world, mesh.faces, process=False)


def _look_at(C: np.ndarray, target: np.ndarray, down_hint=np.array([0, 1.0, 0])) -> tuple[np.ndarray, np.ndarray]:
    f = target - C
    f /= np.linalg.norm(f)
    y = down_hint - f * (down_hint @ f)
    y /= np.linalg.norm(y)
    x = np.cross(y, f)
    R = np.stack([x, y, f])
    return R, -R @ C


def render_photo(arm: LimbModel, view: str, wrist_xy=(95.0, 45.0), seed: int = 0, f35: float = 52.0, height: float = 850.0,
                 size=(1536, 2048), skin=(208, 152, 120), paper="letter") -> bytes:
    rng = np.random.default_rng(seed)
    w, h = size
    diag = np.hypot(w, h)
    f = f35 * diag / 43.27
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])
    center = np.array([SHEET_W / 2, SHEET_H / 2, 0.0])
    C = center + np.array([rng.uniform(-30, 30), rng.uniform(-30, 30), -height])
    R, t = _look_at(C, center + np.array([rng.uniform(-10, 10), rng.uniform(-10, 10), 0]))
    yaw = np.radians(rng.uniform(-6, 6))
    Rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    R, t = Rz @ R, Rz @ t

    # background: table + paper via ray/plane intersection
    sheet_img = np.asarray(marker_sheet(paper, dpi=200)).astype(np.float32)
    pw, ph = PAPER[paper]
    ox, oy = (pw - SHEET_W) / 2, (ph - SHEET_H) / 2
    uu, vv = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    rays = np.stack([uu, vv, np.ones_like(uu)], -1) @ np.linalg.inv(K).T @ R  # world directions
    Cw = -R.T @ t
    lam = -Cw[2] / rays[..., 2]
    P = Cw + rays * lam[..., None]
    X, Y = P[..., 0], P[..., 1]
    px = (X + ox) * 200 / 25.4
    py = (Y + oy) * 200 / 25.4
    on_paper = (X > -ox) & (X < SHEET_W + ox) & (Y > -oy) & (Y < SHEET_H + oy)
    paper_rgb = cv2.remap(sheet_img, px.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    grain = (np.sin(X * 0.08 + np.sin(Y * 0.013) * 3) * 0.5 + 0.5)[..., None]
    table = np.array([150, 112, 82])[None, None, :] * (0.85 + 0.15 * grain)
    bg = np.where(on_paper[..., None], paper_rgb * 0.96, table)
    shade = 0.92 + 0.08 * (1 - ((uu - w * 0.3) ** 2 + (vv - h * 0.3) ** 2) / (w**2 + h**2))
    bg = bg * shade[..., None]

    mesh = _place(arm.to_trimesh(), view, wrist_xy)
    # soft shadow from a light up and to the left
    L = np.array([0.35, 0.25, 1.0])
    L /= np.linalg.norm(L)
    V = mesh.vertices
    S = V - (V[:, 2:3] / L[2]) * L
    Sc = S @ R.T + t
    sp = Sc @ K.T
    sp = sp[:, :2] / sp[:, 2:3]
    smask = np.zeros((h, w), np.uint8)
    for tri in np.round(sp[mesh.faces] * 4).astype(np.int32):
        cv2.fillConvexPoly(smask, tri, 255, shift=2)
    smask = cv2.GaussianBlur(smask.astype(np.float32), (0, 0), 9) / 255.0
    bg = bg * (1 - 0.35 * smask[..., None])

    img = render_perspective(bg.astype(np.uint8), [Layer(mesh, skin)], K, R, t, light=np.array([-0.3, -0.4, -1.0]))
    img = img.astype(np.float32) + rng.normal(0, 2.5, img.shape)
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (0, 0), 0.6)
    im = Image.fromarray(img)
    exif = Image.Exif()
    exif[41989] = int(f35)  # FocalLengthIn35mmFilm
    exif[0x0110] = "Synthetic sample"
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90, exif=exif)
    return buf.getvalue()


def lidar_scan(arm: LimbModel, seed: int = 0) -> trimesh.Trimesh:
    """A 'phone LiDAR' style scan: meters, arbitrary pose, noise and a few holes."""
    rng = np.random.default_rng(seed)
    m = arm.to_trimesh()
    v = m.vertices + rng.normal(0, 0.35, m.vertices.shape)
    keep = np.ones(len(m.faces), bool)
    cent = m.triangles_center
    for _ in range(5):
        c = cent[rng.integers(len(cent))]
        keep &= np.linalg.norm(cent - c, axis=1) > rng.uniform(4, 9)
    m = trimesh.Trimesh(v, m.faces[keep], process=True)
    rot = trimesh.transformations.random_rotation_matrix(rng.random(3))
    m.apply_transform(rot)
    m.apply_translation(rng.uniform(-300, 300, 3))
    m.apply_scale(0.001)
    return m


def truth(arm: LimbModel, hand: dict) -> dict:
    out = {"profiles": {}}
    for z in [0, 20, 40, 60, 80, 100, 120]:
        wd = arm.width_depth(z)
        out["profiles"][str(z)] = {"width": round(wd[0], 2), "depth": round(wd[1], 2), "circumference": round(arm.circumference(z), 2)}
    return out
