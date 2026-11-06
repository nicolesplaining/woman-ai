"""LiDAR mode: clean up an uploaded scan, find the arm's axes, and slice it into the limb model."""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import trimesh
from PIL import Image, ImageDraw

from .design import _font
from .limb import LimbModel, angles, smooth_circular
from .render import Layer, project_ortho, render_ortho


class ScanError(Exception):
    def __init__(self, message: str, retake: str):
        super().__init__(message)
        self.retake = retake


def load_scan(data: bytes, filename: str) -> tuple[trimesh.Trimesh, list[str]]:
    notes = []
    ext = filename.rsplit(".", 1)[-1].lower()
    try:
        m = trimesh.load(io.BytesIO(data), file_type=ext, force="mesh", process=True)
    except Exception as e:
        raise ScanError(f"Couldn't read the scan file ({e}).", "Export the scan as OBJ, STL, PLY or GLB and upload it again.")
    if not isinstance(m, trimesh.Trimesh) or len(m.faces) < 500:
        raise ScanError("The scan has too few triangles.", "Export the scan at a higher detail level.")
    diag = float(np.linalg.norm(m.extents))
    if diag < 3:
        m.apply_scale(1000.0)
        notes.append("Scan was in meters; converted to millimeters.")
    elif diag < 100:
        m.apply_scale(10.0)
        notes.append("Scan looked like centimeters; converted to millimeters.")
    comps = trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(m.faces)), min_len=1)
    if len(comps) > 1:
        big = max(comps, key=len)
        if len(big) < 0.98 * len(m.faces):
            notes.append(f"Removed {len(comps) - 1} loose pieces from the scan.")
        m = m.submesh([big], append=True)
    if len(m.faces) > 120_000:
        try:
            m = m.simplify_quadric_decimation(face_count=120_000)
        except Exception:
            pass
    length = float(np.ptp(m.vertices @ _pca(m)[1][:, 0]))
    if length < 120 or length > 900:
        raise ScanError(
            f"The scan is {length:.0f} mm long, which doesn't look like a forearm.",
            "Crop the scan to just the arm (hand to elbow) in your scanning app before exporting.",
        )
    return m, notes


def _pca(m: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    pts, _ = trimesh.sample.sample_surface(m, 20000, seed=0)
    c = pts.mean(axis=0)
    evals, evecs = np.linalg.eigh(np.cov((pts - c).T))
    order = np.argsort(evals)[::-1]
    return c, evecs[:, order]


@dataclass
class ScanViews:
    images: list[bytes]
    center: np.ndarray
    axes: np.ndarray  # columns e1 (long), e2 (width), e3 (depth)
    s_top: float  # e1 coordinate at the top edge tick 0


def scan_views(m: trimesh.Trimesh) -> ScanViews:
    c, E = _pca(m)
    e1, e2, e3 = E[:, 0], E[:, 1], E[:, 2]
    proj = (m.vertices - c) @ e1
    s_top = float(proj.max())
    imgs = []
    for k, vd in enumerate([-e3, e3]):
        img, info = render_ortho([Layer(m, (215, 170, 140))], vd, e1, size=(640, 900), center=c, bg=(255, 255, 255))
        pil = Image.fromarray(img)
        d = ImageDraw.Draw(pil)
        f = _font(15)
        side = (m.vertices - c) @ np.cross(vd, e1)
        off = float(np.abs(side).max()) + 12
        rvec = np.cross(vd / np.linalg.norm(vd), e1)
        for s in np.arange(0, np.ptp(proj), 10):
            p = c + e1 * (s_top - s) + rvec * off
            a = project_ortho(p[None], info)[0]
            half = 7 if int(s) % 20 == 0 else 4
            d.line([(a[0] - half, a[1]), (a[0] + half, a[1])], fill=(30, 90, 230), width=2)
            if int(s) % 20 == 0:
                d.text((a[0] + 24, a[1]), f"{int(s)}", fill=(20, 60, 200), font=f, anchor="mm")
        d.rectangle([0, 0, pil.width, 26], fill=(255, 255, 255))
        d.text((8, 4), f"Scan view {k + 1}", fill=(0, 0, 0), font=_font(17))
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=88)
        imgs.append(buf.getvalue())
    return ScanViews(imgs, c, E, s_top)


def _radial_sample(segs: np.ndarray, n: int) -> np.ndarray | None:
    """segs: (M, 2, 2). Returns (n, 2) contour sampled by angle around the segment centroid."""
    if len(segs) < 8:
        return None
    p0, p1 = segs[:, 0], segs[:, 1]
    lens = np.linalg.norm(p1 - p0, axis=1)
    c = ((p0 + p1) / 2 * lens[:, None]).sum(0) / max(lens.sum(), 1e-9)
    th = angles(n)
    d = np.stack([np.cos(th), np.sin(th)], axis=1)
    e = p1 - p0
    q = p0 - c

    def cross(a, b):
        return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]

    den = cross(d[:, None, :], e[None, :, :])
    with np.errstate(divide="ignore", invalid="ignore"):
        t = cross(q[None, :, :], e[None, :, :]) / den
        u = cross(q[None, :, :], d[:, None, :]) / den
    ok = (np.abs(den) > 1e-9) & (u >= 0) & (u <= 1) & (t > 0)
    r = np.where(ok, t, np.nan)
    rmax = np.nanmax(np.where(np.isnan(r), -np.inf, r), axis=1)
    rmax[~np.isfinite(rmax)] = np.nan
    good = ~np.isnan(rmax)
    if good.mean() < 0.6:
        return None
    idx = np.arange(n)
    rr = np.interp(idx, np.concatenate([idx[good] - n, idx[good], idx[good] + n]), np.tile(rmax[good], 3))
    rr = smooth_circular(rr, 5)
    return np.stack([c[0] + rr * np.cos(th), c[1] + rr * np.sin(th)], axis=1)


def build_limb(m: trimesh.Trimesh, views: ScanViews, hand_end: str, wrist_tick: float, dorsal_view: int) -> LimbModel:
    E = views.axes
    e1, e3 = E[:, 0], E[:, 2]
    # tick 0 is at the top of the image (max e1); z grows toward the elbow
    if hand_end == "top":
        z_axis = -e1
        wrist_e1 = views.s_top - wrist_tick
    else:
        z_axis = e1
        wrist_e1 = views.s_top - wrist_tick
    y_axis = e3 if dorsal_view == 1 else -e3
    x_axis = np.cross(y_axis, z_axis)
    origin = views.center + e1 * wrist_e1
    R = np.stack([x_axis, y_axis, z_axis])  # rows
    v = (m.vertices - origin) @ R.T
    arm = trimesh.Trimesh(v, m.faces, process=False)
    zmin, zmax = float(v[:, 2].min()) + 2, float(v[:, 2].max()) - 2
    zs, cs = [], []
    for z in np.arange(zmin, zmax, 1.5):
        segs = trimesh.intersections.mesh_plane(arm, plane_normal=[0, 0, 1], plane_origin=[0, 0, z])
        if segs is None or len(segs) == 0:
            continue
        c = _radial_sample(segs[:, :, :2], 180)
        if c is not None:
            zs.append(z)
            cs.append(c)
    if len(zs) < 40:
        raise ScanError("Couldn't slice the scan into a forearm shape.", "Make sure the scan covers the wrist and at least 12 cm of forearm.")
    z = np.array(zs)
    C = np.stack(cs)
    from scipy.ndimage import uniform_filter1d

    C = uniform_filter1d(C, 3, axis=0, mode="nearest")
    limb = LimbModel(z=z, contours=C, source="lidar", uncertainty_mm=1.5)
    limb.measurements["scan_mesh"] = arm
    return limb


def guess_orientation(m: trimesh.Trimesh, views: ScanViews) -> dict:
    """Fallback when Claude is unavailable: the hand end is the narrower end; the wrist is the
    narrowest point 20-55% of the way from it. Dorsal side can't be told reliably."""
    E = views.axes
    e1, e2 = E[:, 0], E[:, 1]
    p1 = (m.vertices - views.center) @ e1
    p2 = (m.vertices - views.center) @ e2
    s = views.s_top - p1  # tick coordinate, 0 at the top
    L = float(s.max())
    bins = np.arange(0, L, 2.0)
    width = np.array([np.ptp(p2[(s >= b) & (s < b + 2)]) if ((s >= b) & (s < b + 2)).sum() > 5 else np.nan for b in bins])
    n = len(bins)
    top_w = np.nanmean(width[: max(n // 7, 1)])
    bot_w = np.nanmean(width[-max(n // 7, 1):])
    hand_end = "top" if top_w < bot_w else "bottom"
    frac = bins / L if hand_end == "top" else 1 - bins / L
    win = (frac > 0.2) & (frac < 0.55) & ~np.isnan(width)
    wrist = float(bins[win][np.argmin(width[win])] + 1) if win.any() else L * 0.3
    return {"hand_end": hand_end, "wrist_tick_mm": wrist, "dorsal_view": 1}
