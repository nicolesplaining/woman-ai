"""Limb model shared by the photo and LiDAR paths, plus contour/loft geometry helpers.

Arm frame (mm):
  z: along the forearm, 0 at the wrist crease, positive toward the elbow (negative = hand)
  x: across the forearm (width)
  y: through the forearm (depth), positive = dorsal (back of the hand)
Each section is a closed contour sampled at N evenly spaced angles, counter-clockwise from +x.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import manifold3d
import numpy as np
import trimesh


@dataclass
class LimbModel:
    z: np.ndarray  # (K,) increasing
    contours: np.ndarray  # (K, N, 2)
    source: str  # "photo" or "lidar"
    uncertainty_mm: float
    measurements: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def n_angles(self) -> int:
        return self.contours.shape[1]

    def centers(self) -> np.ndarray:
        return self.contours.mean(axis=1)

    def sections_at(self, z_new: np.ndarray) -> np.ndarray:
        """Linearly interpolate contours at new z positions (clamped to the model range)."""
        z_new = np.clip(np.asarray(z_new, float), self.z[0], self.z[-1])
        idx = np.clip(np.searchsorted(self.z, z_new) - 1, 0, len(self.z) - 2)
        z0, z1 = self.z[idx], self.z[idx + 1]
        t = ((z_new - z0) / np.maximum(z1 - z0, 1e-9))[:, None, None]
        return self.contours[idx] * (1 - t) + self.contours[idx + 1] * t

    def width_depth(self, z: float) -> tuple[float, float]:
        c = self.sections_at(np.array([z]))[0]
        return float(np.ptp(c[:, 0])), float(np.ptp(c[:, 1]))

    def circumference(self, z: float) -> float:
        return polygon_perimeter(self.sections_at(np.array([z]))[0])

    def to_trimesh(self) -> trimesh.Trimesh:
        v, f = loft(self.z, self.contours)
        return trimesh.Trimesh(v, f, process=False)


def angles(n: int) -> np.ndarray:
    return np.linspace(0.0, 2 * np.pi, n, endpoint=False)


def superellipse_contour(cx: float, cy: float, half_w: float, half_d: float, n: int, p: float = 2.0) -> np.ndarray:
    """Superellipse |x/a|^p + |y/b|^p = 1 sampled by polar angle. p=2 is an ellipse."""
    th = angles(n)
    c, s = np.cos(th), np.sin(th)
    r = (np.abs(c / half_w) ** p + np.abs(s / half_d) ** p) ** (-1.0 / p)
    return np.stack([cx + r * c, cy + r * s], axis=1)


def ellipse_perimeter(a: float, b: float) -> float:
    """Ramanujan's second approximation (semi-axes a, b)."""
    h = ((a - b) / (a + b)) ** 2
    return float(np.pi * (a + b) * (1 + 3 * h / (10 + np.sqrt(4 - 3 * h))))


def polygon_perimeter(c: np.ndarray) -> float:
    return float(np.linalg.norm(np.roll(c, -1, axis=0) - c, axis=1).sum())


def smooth_circular(values: np.ndarray, window: int, axis: int = 0) -> np.ndarray:
    """Moving average with wrap-around along `axis`."""
    if window <= 1:
        return values
    k = np.ones(window) / window
    pad = window // 2
    v = np.moveaxis(values, axis, -1)
    padded = np.concatenate([v[..., -pad:], v, v[..., :pad]], axis=-1)
    out = np.apply_along_axis(lambda r: np.convolve(r, k, mode="valid"), -1, padded)
    out = out[..., : v.shape[-1]]
    return np.moveaxis(out, -1, axis)


def contour_normals(c: np.ndarray) -> np.ndarray:
    """Outward unit normals of CCW closed contours. c: (..., N, 2)."""
    t = np.roll(c, -1, axis=-2) - np.roll(c, 1, axis=-2)
    n = np.stack([t[..., 1], -t[..., 0]], axis=-1)
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)


def offset_contours(c: np.ndarray, d) -> np.ndarray:
    """Offset contours outward by d (scalar or broadcastable to (..., N))."""
    d = np.asarray(d, float)
    return c + contour_normals(c) * d[..., None]


def resample_polar(c: np.ndarray, n: int, center: np.ndarray | None = None) -> np.ndarray:
    """Resample a star-shaped closed contour at n evenly spaced polar angles around its center."""
    if center is None:
        center = c.mean(axis=0)
    d = c - center
    phi = np.mod(np.arctan2(d[:, 1], d[:, 0]), 2 * np.pi)
    r = np.linalg.norm(d, axis=1)
    order = np.argsort(phi)
    phi, r = phi[order], r[order]
    phi_ext = np.concatenate([phi - 2 * np.pi, phi, phi + 2 * np.pi])
    r_ext = np.concatenate([r, r, r])
    th = angles(n)
    rr = np.interp(th, phi_ext, r_ext)
    return np.stack([center[0] + rr * np.cos(th), center[1] + rr * np.sin(th)], axis=1)


def loft(z: np.ndarray, contours: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Closed, outward-facing triangle mesh through CCW contours at increasing z, with end caps."""
    k, n = contours.shape[:2]
    verts = np.concatenate(
        [contours.reshape(-1, 2), np.repeat(z, n)[:, None]], axis=1
    )
    centers = np.concatenate([contours.mean(axis=1), z[:, None]], axis=1)
    c0 = len(verts)
    c1 = c0 + 1
    verts = np.vstack([verts, centers[0], centers[-1]])

    j = np.arange(n)
    jn = (j + 1) % n
    faces = []
    for i in range(k - 1):
        a = i * n + j
        b = i * n + jn
        c = (i + 1) * n + j
        d = (i + 1) * n + jn
        faces.append(np.stack([a, b, c], axis=1))
        faces.append(np.stack([b, d, c], axis=1))
    faces.append(np.stack([np.full(n, c0), jn, j], axis=1))
    last = (k - 1) * n
    faces.append(np.stack([np.full(n, c1), last + j, last + jn], axis=1))
    return verts, np.vstack(faces)


def to_manifold(verts: np.ndarray, faces: np.ndarray) -> manifold3d.Manifold:
    mesh = manifold3d.Mesh(
        vert_properties=np.ascontiguousarray(verts, dtype=np.float32),
        tri_verts=np.ascontiguousarray(faces, dtype=np.uint32),
    )
    m = manifold3d.Manifold(mesh)
    if m.status() != manifold3d.Error.NoError:
        raise ValueError(f"mesh is not a valid solid: {m.status()}")
    return m


def from_manifold(m: manifold3d.Manifold) -> trimesh.Trimesh:
    mesh = m.to_mesh()
    return trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), process=False)


def trimesh_to_manifold(t: trimesh.Trimesh) -> manifold3d.Manifold:
    t = t.copy()
    t.merge_vertices()
    return to_manifold(t.vertices, t.faces)


def loft_manifold(z: np.ndarray, contours: np.ndarray) -> manifold3d.Manifold:
    return to_manifold(*loft(z, contours))
