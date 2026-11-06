"""Paraglider hand parts rendered with OpenSCAD (WebAssembly build, run through Node)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import trimesh

BACKEND = Path(__file__).resolve().parent.parent
SCAD_DIR = BACKEND / "scad"
DESIGN_DIR = SCAD_DIR / "paraglider"

# Measured from the patched Paraglider files rendered at 100% (palm sections and finger pivot positions).
PALM_BASE = {
    "wrist_outside_width": 61.5,  # palm width at the wrist pins
    "wrist_inside_width": 51.6,  # from the Paraglider README (64 mm at 124%)
    "knuckle_breadth": 62.0,  # palm width just behind the knuckles
    "palm_inside_width": 56.0,  # estimate: knuckle breadth minus ~3 mm walls
    "hand_length": 141.6,  # wrist pin to tip of the long finger (77.5 palm + 24.1 phalanx + 40.0 tip)
}

# (part id, label, count, scad file, extra defines)
FINGER_FLAGS = ["print_long_fingers", "print_short_fingers", "print_finger_phalanx", "print_thumb", "print_thumb_phalanx"]


def _finger_job(flag: str) -> dict:
    return {k: (k == flag) for k in FINGER_FLAGS}


HAND_PARTS = [
    ("palm", "Palm", 1),
    ("long_finger", "Long fingertip (index, middle)", 2),
    ("short_finger", "Short fingertip (ring, little)", 2),
    ("finger_phalanx", "Finger phalanx", 4),
    ("thumb", "Thumb tip", 1),
    ("thumb_phalanx", "Thumb phalanx", 1),
    ("tensioner", "Tensioner box", 1),
]


def run_scad(jobs: list[dict]) -> list[dict]:
    payload = json.dumps({"designDir": str(DESIGN_DIR), "jobs": jobs})
    proc = subprocess.run(
        ["node", str(SCAD_DIR / "run.mjs")],
        input=payload,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(SCAD_DIR),
    )
    results = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    if len(results) != len(jobs):
        raise RuntimeError(f"OpenSCAD runner failed: {proc.stderr[-2000:]}")
    return results


def render_hand(out_dir: Path, length_scale: float, width_ratio: float, right_hand: bool) -> dict:
    """Render all hand parts. Returns {part_id: {"path", "label", "count"}} plus the track for the gauntlet."""
    out_dir.mkdir(parents=True, exist_ok=True)
    s = round(length_scale, 3)
    r = round(width_ratio, 3)
    jobs = [
        {
            "main": "paraglider_palm_left.scad",
            "out": str(out_dir / "palm.stl"),
            "defines": {
                "overall_scale": s,
                "width_ratio": r,
                "mirrored": right_hand,
                "include_wrist_stamping_die": False,
                "pivot_size": 3.0,
            },
        }
    ]
    finger_defs = {"global_scale": s, "width_ratio": r, "pin_index": 0}
    for pid, flag in [
        ("long_finger", "print_long_fingers"),
        ("short_finger", "print_short_fingers"),
        ("finger_phalanx", "print_finger_phalanx"),
        ("thumb", "print_thumb"),
        ("thumb_phalanx", "print_thumb_phalanx"),
    ]:
        jobs.append({"main": "fingerator.scad", "out": str(out_dir / f"{pid}.stl"), "defines": {**finger_defs, **_finger_job(flag)}})
    jobs.append({"main": "gripper_box_pieces.scad", "out": str(out_dir / "tensioner.stl"), "defines": {"global_scale": s}})
    jobs.append({"code": f"use <thermo_gauntlet.scad>\nscale({s}) track_block();", "out": str(out_dir / "_track.stl"), "defines": {}})

    results = run_scad(jobs)
    failed = [Path(r["out"]).stem for r in results if not r["ok"]]
    if failed:
        raise RuntimeError(f"OpenSCAD could not render: {', '.join(failed)}")

    parts = {}
    for pid, label, count in HAND_PARTS:
        parts[pid] = {"path": out_dir / f"{pid}.stl", "label": label, "count": count}
    track = trimesh.load(out_dir / "_track.stl", force="mesh")
    return {"parts": parts, "track": track, "render_ms": sum(r["ms"] for r in results)}


# ---------------------------------------------------------------- assembled preview (for the 3D view only)
import numpy as np  # noqa: E402

# Palm frame (left palm, 100%): x across, y toward the fingers, z up (dorsal). Knuckle pin positions.
KNUCKLES = [(13.7, 39.5, 6.0, "long_finger"), (-0.3, 39.5, 6.0, "long_finger"), (-14.3, 35.5, 6.0, "short_finger"), (-28.3, 29.5, 6.0, "short_finger")]
THUMB_KNUCKLE = (31.0, 0.0, 6.0)
WRIST_PIN_CENTER = (-7.36, -38.0, 8.0)
# Pivot positions inside each fingerator output (x offsets are not scaled; y, z are).
PHALANX_PIVOTS = ((0.0, -11.8, 6.0), (0.0, 12.3, 5.8))
TIP_PIVOT = {"long_finger": (-25.0, -20.0, 9.0), "short_finger": (-50.0, -18.0, 9.0), "thumb": (60.0, -15.4, 6.48)}
THUMB_PHALANX_X = 30.0


def assemble_preview(hand_dir: Path, length_scale: float, width_ratio: float, right_hand: bool, hinge_xyz) -> dict:
    """Place palm + fingers (straight) on the arm, hinge pins on the gauntlet's hinge axis.
    Returns {"palm", "tips", "phalanges"} meshes in the arm frame (x across, y dorsal, z toward elbow)."""
    s = length_scale
    sx = s * width_ratio
    m = -1.0 if right_hand else 1.0
    load = lambda n: trimesh.load(hand_dir / f"{n}.stl", force="mesh")  # noqa: E731

    def sc(p):  # palm-frame point at 100% -> scaled
        return np.array([p[0] * sx * m, p[1] * s, p[2] * s])

    tips, phal = [], []
    prox = np.array([0.0, PHALANX_PIVOTS[0][1] * s, PHALANX_PIVOTS[0][2] * s])
    dist = np.array([0.0, PHALANX_PIVOTS[1][1] * s, PHALANX_PIVOTS[1][2] * s])
    base = {k: load(k) for k in ("finger_phalanx", "long_finger", "short_finger", "thumb", "thumb_phalanx")}
    for kx, ky, kz, tip in KNUCKLES:
        K = sc((kx, ky, kz))
        ph = base["finger_phalanx"].copy()
        ph.apply_translation(K - prox)
        phal.append(ph)
        tp = TIP_PIVOT[tip]
        t = base[tip].copy()
        t.apply_translation(K - prox + dist - np.array([tp[0], tp[1] * s, tp[2] * s]))
        tips.append(t)
    # thumb: build at the origin, angle it outward, move to its knuckle
    tph = base["thumb_phalanx"].copy()
    tph.apply_translation(-np.array([THUMB_PHALANX_X, prox[1], prox[2]]))
    tt = base["thumb"].copy()
    tp = TIP_PIVOT["thumb"]
    tt.apply_translation(dist - np.array([tp[0], tp[1] * s, tp[2] * s]))
    rot = trimesh.transformations.rotation_matrix(np.radians(-40.0 * m), [0, 0, 1])
    Kt = sc(THUMB_KNUCKLE)
    for part, bucket in ((tph, phal), (tt, tips)):
        part.apply_transform(rot)
        part.apply_translation(Kt)
        bucket.append(part)

    palm = load("palm")
    pin = sc(WRIST_PIN_CENTER)
    H = np.asarray(hinge_xyz, float)
    # palm frame -> arm frame: x -> x, z (dorsal) -> y, y (toward fingers) -> -z ; pin center -> hinge
    M = np.eye(4)
    M[:3, :3] = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)
    M[:3, 3] = H - M[:3, :3] @ pin
    out = {}
    for name, meshes in (("palm", [palm]), ("tips", tips), ("phalanges", phal)):
        mm = trimesh.util.concatenate(meshes)
        mm.apply_transform(M)
        out[name] = mm
    return out
