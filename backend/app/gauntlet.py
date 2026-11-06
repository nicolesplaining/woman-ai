"""Custom-fit gauntlet: a C-shaped shell offset from the limb, with hinge tabs that match the
scaled Paraglider palm, the stock tensioner track, strap slots, and an optional surface pattern."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import manifold3d
import numpy as np
import trimesh
from scipy.ndimage import map_coordinates

from .hand import PALM_BASE
from .limb import (
    LimbModel,
    angles,
    contour_normals,
    from_manifold,
    loft_manifold,
    offset_contours,
    polygon_perimeter,
    resample_polar,
    trimesh_to_manifold,
)

# Builds a height map (mm) over a (u, v) grid: rows are u (around the arm), columns are v (z).
PatternBuilder = Callable[[float, float, float, float, float], np.ndarray]

PIN_HOLE_D = 3.5  # M3 screw clearance
TAB_THICKNESS = 4.0
TAB_CLEARANCE = 0.4  # between the palm's wrist joint and the tab
SLOT_W, SLOT_L = 4.0, 27.0  # 1 inch velcro


@dataclass
class GauntletParams:
    width_scale: float
    length_scale: float
    gap_mm: float
    wall_mm: float = 3.0
    hinge_z: float = 0.0
    body_offset_mm: float = 18.0  # hinge to the distal edge of the shell
    length_mm: Optional[float] = None
    opening_deg: float = 110.0  # angular size of the volar opening
    pattern_height_mm: float = 0.0  # >0 raised, <0 recessed
    name_side: str = "+x"


@dataclass
class GauntletResult:
    gauntlet: trimesh.Trimesh
    fit_ring: trimesh.Trimesh
    info: dict = field(default_factory=dict)


def _section_frames(limb: LimbModel, z: np.ndarray, n: int) -> np.ndarray:
    secs = limb.sections_at(z)
    return np.stack([resample_polar(s, n) for s in secs])


def _arc_u(contours: np.ndarray) -> np.ndarray:
    """Signed arc length per vertex from the dorsal point (theta = 90 deg), positive toward +x."""
    k, n = contours.shape[:2]
    seg = np.linalg.norm(np.roll(contours, -1, axis=1) - contours, axis=2)
    cum = np.concatenate([np.zeros((k, 1)), np.cumsum(seg, axis=1)[:, :-1]], axis=1)
    j_dorsal = n // 4
    u = -(cum - cum[:, [j_dorsal]])  # CCW is toward -x, so flip
    per = seg.sum(axis=1, keepdims=True)
    u = (u + per / 2) % per - per / 2  # wrap into [-per/2, per/2)
    return u


def _opening_cutter(centers: np.ndarray, z: np.ndarray, opening_deg: float) -> manifold3d.Manifold:
    half = np.radians(opening_deg) / 2
    th = np.linspace(-np.pi / 2 - half, -np.pi / 2 + half, 40)
    rho = 400.0
    polys = []
    for c in centers:
        arc = np.stack([c[0] + rho * np.cos(th), c[1] + rho * np.sin(th)], axis=1)
        polys.append(np.vstack([c, arc]))
    return loft_manifold(z, np.stack(polys))


def _rot_z(deg: float) -> np.ndarray:
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1.0]])


def _slot(center: np.ndarray, normal2d: np.ndarray) -> manifold3d.Manifold:
    box = manifold3d.Manifold.cube([SLOT_W, 24.0, SLOT_L], True)
    ang = np.degrees(np.arctan2(normal2d[1], normal2d[0])) - 90.0
    return box.rotate([0, 0, ang]).translate(list(center))


def build_gauntlet(
    limb: LimbModel,
    p: GauntletParams,
    track_plate_mesh: Optional[trimesh.Trimesh] = None,
    pattern_builder: Optional[PatternBuilder] = None,
) -> GauntletResult:
    warnings: list[str] = []
    s_w, s_l = p.width_scale, p.length_scale
    z_start = p.hinge_z + p.body_offset_mm
    length = p.length_mm or float(np.clip(75.0 * s_l, 60.0, 150.0))
    z_end = z_start + length
    if z_end > limb.z[-1] - 2:
        new_end = limb.z[-1] - 2
        warnings.append(
            f"The photos/scan only cover {limb.z[-1]:.0f} mm of forearm, so the gauntlet was shortened "
            f"from {length:.0f} to {new_end - z_start:.0f} mm."
        )
        z_end = new_end
        length = z_end - z_start
    if length < 40:
        raise ValueError("Not enough forearm is visible to build a gauntlet (need at least ~60 mm past the wrist).")

    # Inner cutter (limb + padding gap), extended past both ends.
    z_in = np.arange(min(p.hinge_z - 30, z_start - 30), z_end + 30, 1.5)
    sec_in = _section_frames(limb, z_in, 240)
    inner_c = offset_contours(sec_in, p.gap_mm)
    inner = loft_manifold(z_in, inner_c)
    centers_in = sec_in.mean(axis=1)

    # Outer surface, finely sampled when a pattern is applied.
    fine = pattern_builder is not None and abs(p.pattern_height_mm) > 0
    dz = 0.6 if fine else 1.5
    z_out = np.arange(z_start, z_end + 1e-6, dz)
    base = offset_contours(_section_frames(limb, z_out, 240), p.gap_mm + p.wall_mm)
    per_max = max(polygon_perimeter(c) for c in base)
    n_out = int(np.ceil(per_max / (0.6 if fine else 1.4) / 8) * 8)
    base = offset_contours(_section_frames(limb, z_out, n_out), p.gap_mm + p.wall_mm)
    th = angles(n_out)

    # Geometry needed for features and keep-out zones.
    half_open = np.radians(p.opening_deg) / 2
    edge_th = [-np.pi / 2 - half_open, -np.pi / 2 + half_open]
    centers_out = base.mean(axis=1)
    u = _arc_u(base)
    v = np.repeat(z_out[:, None], n_out, axis=1)

    track_scale = s_l
    track_half_w = 13.5 * track_scale
    track_z0 = z_start + 5.0
    track_z1 = track_z0 + 55.0 * track_scale
    if track_z1 > z_end - 2:
        track_z1 = z_end - 2
    strap_z = [z_start + 0.30 * length, z_start + 0.78 * length]
    slot_th = [edge_th[0] - np.radians(16), edge_th[1] + np.radians(16)]

    info = {
        "z_start": z_start,
        "z_end": z_end,
        "length_mm": length,
        "gap_mm": p.gap_mm,
        "wall_mm": p.wall_mm,
        "opening_deg": p.opening_deg,
        "strap_z": strap_z,
        "pattern": False,
    }

    disp = np.zeros_like(u)
    if fine:
        res = 0.25
        u_min, u_max = float(u.min()) - 1, float(u.max()) + 1
        v_min, v_max = z_start, z_end
        hmap = pattern_builder(u_min, u_max, v_min, v_max, res)
        uu = np.arange(hmap.shape[0]) * res + u_min
        vv = np.arange(hmap.shape[1]) * res + v_min
        UU, VV = np.meshgrid(uu, vv, indexing="ij")
        keep = np.ones_like(hmap, dtype=bool)
        keep &= (VV > z_start + 6) & (VV < z_end - 6)
        # opening edges: convert edge angles to u at each z (use mid-length section)
        mid = len(z_out) // 2
        for e in edge_th:
            j = int(np.round((np.mod(e, 2 * np.pi)) / (2 * np.pi) * n_out)) % n_out
            u_edge = float(u[mid, j])
            keep &= np.abs(UU - u_edge) > 10
            # also the far side of the opening (everything between the edges is cut anyway)
        # dorsal tensioner track
        keep &= ~((np.abs(UU) < track_half_w + 4) & (VV > track_z0 - 4) & (VV < track_z1 + 4))
        # strap slots
        for t_s in slot_th:
            j = int(np.round((np.mod(t_s, 2 * np.pi)) / (2 * np.pi) * n_out)) % n_out
            u_s = float(u[mid, j])
            for zs in strap_z:
                keep &= ~((np.abs(UU - u_s) < SLOT_W / 2 + 4) & (np.abs(VV - zs) < SLOT_L / 2 + 4))
        # hinge tab attachments at the sides
        for t_t in [0.0, np.pi]:
            j = int(np.round(t_t / (2 * np.pi) * n_out)) % n_out
            u_t = float(u[mid, j])
            keep &= ~((np.abs(UU - u_t) < 14) & (VV < z_start + 26))
        hmap = hmap * keep
        coords = np.stack([(u - u_min) / res, (v - v_min) / res])
        disp = map_coordinates(hmap, coords.reshape(2, -1), order=1, mode="constant").reshape(u.shape)
        disp *= p.pattern_height_mm
        info["pattern"] = True
        info["pattern_coverage"] = float((np.abs(disp) > 0.05).mean())

    outer_c = base + contour_normals(base) * disp[..., None]
    outer = loft_manifold(z_out, outer_c)

    shell = outer - inner
    shell = shell - _opening_cutter(centers_in, z_in, p.opening_deg)
    plain_shell = shell

    # Hinge tabs: their inner faces sit just outside the palm's wrist joint.
    wrist_c = _section_frames(limb, np.array([p.hinge_z]), 240)[0].mean(axis=0)
    palm_half = PALM_BASE["wrist_outside_width"] / 2 * s_w
    boss_r = 5.0 * s_l + 1.5
    tab_x = palm_half + TAB_CLEARANCE + TAB_THICKNESS / 2
    z_attach = z_start + 12.0
    j_side = {+1: 0, -1: n_out // 2}
    k_attach = int(np.argmin(np.abs(z_out - z_attach)))
    feats = []
    holes = []
    for s in (+1, -1):
        hinge = np.array([wrist_c[0] + s * tab_x, wrist_c[1], p.hinge_z])
        boss = (
            manifold3d.Manifold.cylinder(TAB_THICKNESS, boss_r, boss_r, 48, True)
            .rotate([0, 90, 0])
            .translate(list(hinge))
        )
        side_pt = base[k_attach, j_side[s]]
        inward = side_pt - s * np.array([p.wall_mm * 0.8, 0.0])
        pad = manifold3d.Manifold.cube([TAB_THICKNESS, 2 * boss_r, 16.0], True).translate(
            [float(inward[0]), float(inward[1]), z_attach]
        )
        feats.append(manifold3d.Manifold.batch_hull([boss, pad]))
        holes.append(
            manifold3d.Manifold.cylinder(40.0, PIN_HOLE_D / 2, PIN_HOLE_D / 2, 24, True)
            .rotate([0, 90, 0])
            .translate(list(hinge))
        )

    # Tensioner track on the dorsal side, on a pedestal that fills down to the curved shell.
    if track_plate_mesh is not None:
        k0, k1 = np.searchsorted(z_out, [track_z0, track_z1])
        region = base[k0:k1]
        rel_x = region[..., 0] - centers_out[k0:k1, None, 0]
        sel = np.abs(rel_x) <= track_half_w
        y_top = float(region[..., 1][sel].max()) if sel.any() else float(base[..., 1].max())
        # plate frame: x across, y along (front +y toward hand), z up from plate (bottom at 2*s)
        t = track_plate_mesh.copy()
        pv = t.vertices.copy()
        y_front = pv[:, 1].max()
        z_bot = pv[:, 2].min()
        arm = np.empty_like(pv)
        arm[:, 0] = pv[:, 0] + centers_out[(k0 + k1) // 2, 0]
        arm[:, 1] = y_top + (pv[:, 2] - z_bot)
        arm[:, 2] = track_z0 + (y_front - pv[:, 1])
        # mapping (x, y, z) -> (x, z_plate, -y_plate) has det +1, so faces keep their orientation
        t = trimesh.Trimesh(arm, t.faces, process=True)
        track_len = float(np.ptp(arm[:, 2]))
        try:
            feats.append(trimesh_to_manifold(t))
        except ValueError:
            warnings.append("Couldn't attach the stock tensioner track; print the stock tensioner instead.")
        ped = manifold3d.Manifold.cube([2 * track_half_w, 14.0, track_len], False).translate(
            [float(centers_out[(k0 + k1) // 2, 0] - track_half_w), y_top - 14.0 + 0.05, float(arm[:, 2].min())]
        )
        feats.append(ped)
        info["track_z"] = [float(arm[:, 2].min()), float(arm[:, 2].max())]

    g = manifold3d.Manifold.batch_boolean([shell, *feats], manifold3d.OpType.Add)
    g = g - inner
    g = g - _opening_cutter(centers_in, z_in, p.opening_deg)
    for h in holes:
        g = g - h

    # Strap slots near both edges of the opening.
    mid_k = len(z_out) // 2
    for t_s in slot_th:
        j = int(np.round((np.mod(t_s, 2 * np.pi)) / (2 * np.pi) * n_out)) % n_out
        for zs in strap_z:
            k = int(np.argmin(np.abs(z_out - zs)))
            sec = base[k]
            nrm = contour_normals(sec)[j]
            mid_pt = sec[j] - nrm * p.wall_mm / 2
            g = g - _slot(np.array([mid_pt[0], mid_pt[1], zs]), nrm)

    # Fit-test ring from the middle of the plain shell.
    z_mid = (z_start + z_end) / 2
    slab = manifold3d.Manifold.cube([600.0, 600.0, 15.0], True).translate([0.0, 0.0, z_mid])
    ring = plain_shell ^ slab
    info["fit_ring_z"] = z_mid

    gm = from_manifold(g)
    rm = from_manifold(ring)
    info["warnings"] = warnings
    info["hinge"] = {
        "z": p.hinge_z,
        "center": [float(wrist_c[0]), float(wrist_c[1]), float(p.hinge_z)],
        "tab_inner_halfwidth": palm_half + TAB_CLEARANCE,
        "pin_hole_d": PIN_HOLE_D,
    }
    info["circumference_inner_mid"] = polygon_perimeter(
        offset_contours(_section_frames(limb, np.array([z_mid]), 240), p.gap_mm)[0]
    )
    return GauntletResult(gauntlet=gm, fit_ring=rm, info=info)
