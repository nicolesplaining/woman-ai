"""Tiny software renderer (painter's algorithm + Lambert shading) for previews, Claude views,
and synthetic sample photos. Good enough for tens of thousands of triangles."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import trimesh


@dataclass
class Layer:
    mesh: trimesh.Trimesh
    color: tuple[int, int, int]  # RGB
    alpha: float = 1.0


def _look_basis(view_dir: np.ndarray, up: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d = view_dir / np.linalg.norm(view_dir)
    r = np.cross(d, up)
    r /= np.linalg.norm(r)
    u = np.cross(r, d)
    return r, u, d


def _draw(canvas: np.ndarray, pts2d: np.ndarray, depth: np.ndarray, shade: np.ndarray, color, alpha: float):
    order = np.argsort(-depth)
    col = np.asarray(color, float)
    pts = np.round(pts2d * 4).astype(np.int32)  # 2 bits of sub-pixel precision
    if alpha >= 1.0:
        for i in order:
            c = tuple(int(v) for v in np.clip(col * shade[i], 0, 255))
            cv2.fillConvexPoly(canvas, pts[i], c, lineType=cv2.LINE_8, shift=2)
    else:
        layer = canvas.copy()
        mask = np.zeros(canvas.shape[:2], np.uint8)
        for i in order:
            c = tuple(int(v) for v in np.clip(col * shade[i], 0, 255))
            cv2.fillConvexPoly(layer, pts[i], c, lineType=cv2.LINE_8, shift=2)
            cv2.fillConvexPoly(mask, pts[i], 255, lineType=cv2.LINE_8, shift=2)
        m = (mask > 0)[..., None]
        canvas[:] = np.where(m, (canvas * (1 - alpha) + layer * alpha).astype(canvas.dtype), canvas)


def render_ortho(
    layers: list[Layer],
    view_dir,
    up,
    size: tuple[int, int] = (800, 800),
    px_per_mm: float | None = None,
    center=None,
    bg=(250, 250, 250),
    light=None,
    supersample: int = 2,
) -> tuple[np.ndarray, dict]:
    """Orthographic render. Returns (RGB image, projection info for mapping mm -> px)."""
    view_dir = np.asarray(view_dir, float)
    r, u, d = _look_basis(view_dir, np.asarray(up, float))
    allv = np.vstack([l.mesh.vertices for l in layers])
    if center is None:
        center = (allv.min(0) + allv.max(0)) / 2
    center = np.asarray(center, float)
    w, h = size
    if px_per_mm is None:
        ext_r = np.ptp((allv - center) @ r)
        ext_u = np.ptp((allv - center) @ u)
        px_per_mm = 0.9 * min(w / max(ext_r, 1e-6), h / max(ext_u, 1e-6))
    S = supersample
    canvas = np.full((h * S, w * S, 3), bg, np.uint8)
    light = np.asarray(light if light is not None else (-0.4 * r + 0.6 * u - 0.7 * d), float)
    light /= np.linalg.norm(light)
    for l in layers:
        m = l.mesh
        rel = m.vertices - center
        px = np.stack([(rel @ r) * px_per_mm + w / 2, -(rel @ u) * px_per_mm + h / 2], axis=1) * S
        tri = px[m.faces]
        depth = (rel @ d)[m.faces].mean(axis=1)
        n = m.face_normals
        front = (n @ d) < 0
        shade = 0.3 + 0.7 * np.clip(n @ light, 0, 1)
        _draw(canvas, tri[front], depth[front], shade[front], l.color, l.alpha)
    img = cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA)
    info = {"right": r, "up": u, "dir": d, "center": center, "px_per_mm": px_per_mm, "size": size}
    return img, info


def project_ortho(points: np.ndarray, info: dict) -> np.ndarray:
    rel = np.asarray(points, float) - info["center"]
    w, h = info["size"]
    return np.stack([(rel @ info["right"]) * info["px_per_mm"] + w / 2, -(rel @ info["up"]) * info["px_per_mm"] + h / 2], axis=1)


def render_perspective(
    canvas: np.ndarray,
    layers: list[Layer],
    K: np.ndarray,
    R: np.ndarray,
    t: np.ndarray,
    light,
    supersample: int = 2,
) -> np.ndarray:
    """Draw meshes over an existing RGB canvas with a pinhole camera x = K (R X + t)."""
    h, w = canvas.shape[:2]
    S = supersample
    big = cv2.resize(canvas, (w * S, h * S), interpolation=cv2.INTER_LINEAR)
    light = np.asarray(light, float)
    light /= np.linalg.norm(light)
    cam_center = -R.T @ t
    for l in layers:
        m = l.mesh
        Xc = m.vertices @ R.T + t
        p = Xc @ K.T
        px = p[:, :2] / p[:, 2:3] * S
        tri = px[m.faces]
        depth = Xc[:, 2][m.faces].mean(axis=1)
        n = m.face_normals
        centers = m.triangles_center
        front = np.einsum("ij,ij->i", n, centers - cam_center) < 0
        shade = 0.45 + 0.55 * np.clip(n @ light, 0, 1)
        _draw(big, tri[front], depth[front], shade[front], l.color, l.alpha)
    return cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA)
