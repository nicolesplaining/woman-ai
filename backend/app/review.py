"""Claude's render review: look at the gauntlet on the arm and nudge a few bounded settings.

Claude can change only four things (padding gap, gauntlet length, opening angle, strap positions),
every value is clamped to a fixed range, and there are at most MAX_PASSES reviews. Everything else,
including all measurements and sizing, stays exactly as the code computed it.
"""
from __future__ import annotations

import io
from dataclasses import replace
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw

MAX_PASSES = 3

# Smallest change worth a rebuild, per setting.
MIN_STEP = {"gap_mm": 0.2, "gauntlet_length_mm": 2.0, "opening_deg": 2.0, "strap_near": 0.01, "strap_far": 0.01}


def review_bounds(gap: tuple[float, float], length: tuple[float, float]) -> dict:
    return {
        "gap_mm": [float(gap[0]), float(gap[1])],
        "gauntlet_length_mm": [float(length[0]), float(length[1])],
        "opening_deg": [90.0, 130.0],
        "strap_near": [0.20, 0.40],  # fraction of the gauntlet length from the hand end
        "strap_far": [0.65, 0.88],
    }


def settings_of(p) -> dict:
    return {
        "gap_mm": round(p.gap_mm, 2),
        "gauntlet_length_mm": round(p.length_mm, 1) if p.length_mm else None,
        "opening_deg": round(p.opening_deg, 1),
        "strap_near": round(p.strap_positions[0], 3),
        "strap_far": round(p.strap_positions[1], 3),
    }


def apply_adjustments(p, adjustments: dict, bounds: dict):
    """Clamp Claude's requested values to bounds. Returns (new params, list of human-readable changes)."""
    cur = settings_of(p)
    new = dict(cur)
    changes = []
    for key, (lo, hi) in bounds.items():
        val = (adjustments or {}).get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
            continue
        val = float(np.clip(val, lo, hi))
        if cur[key] is None or abs(val - cur[key]) >= MIN_STEP[key]:
            new[key] = val
            changes.append(f"{key} {cur[key]} -> {round(val, 3)}")
    if not changes:
        return p, []
    near, far = new["strap_near"], new["strap_far"]
    p2 = replace(
        p,
        gap_mm=new["gap_mm"],
        length_mm=new["gauntlet_length_mm"],
        opening_deg=new["opening_deg"],
        strap_positions=(min(near, far), max(near, far)),
    )
    return p2, changes


def review_loop(params, build: Callable, render: Callable, ask: Callable, bounds: dict, first_result=None, max_passes: int = MAX_PASSES):
    """build(params) -> result; render(result) -> [jpeg bytes]; ask(images, settings, bounds) -> verdict dict.

    Returns (final result, final params, history of passes).
    """
    result = first_result if first_result is not None else build(params)
    history = []
    for n in range(1, max_passes + 1):
        verdict = ask(render(result), settings_of(params), bounds) or {}
        new_params, changes = ({}, [])
        if not verdict.get("looks_good"):
            new_params, changes = apply_adjustments(params, verdict.get("adjustments") or {}, bounds)
        history.append({"pass": n, "looks_good": bool(verdict.get("looks_good")), "issues": verdict.get("issues") or [], "changes": changes, "summary": verdict.get("summary", "")})
        if not changes:
            break
        params = new_params
        result = build(params)
    return result, params, history


# ---------------------------------------------------------------- renders for Claude

VIEWS = [
    ("Back of the forearm", (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
    ("Palm side (the opening)", (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),
    ("Side view", (-1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
]


def render_fit(arm_mesh, gauntlet_mesh, size=(560, 640)) -> list[bytes]:
    """Three labelled views of the gauntlet over the arm (arm frame: z toward the elbow, y dorsal)."""
    from .render import Layer, render_ortho

    out = []
    for title, view_dir, up in VIEWS:
        img, _ = render_ortho(
            [Layer(arm_mesh, (214, 170, 140)), Layer(gauntlet_mesh, (90, 150, 230))],
            np.array(view_dir),
            np.array(up),
            size=size,
            bg=(255, 255, 255),
        )
        pil = Image.fromarray(img)
        d = ImageDraw.Draw(pil)
        d.rectangle([0, 0, pil.width, 24], fill=(255, 255, 255))
        d.text((8, 5), f"{title}. Skin = arm, blue = gauntlet. Hand end at the top.", fill=(0, 0, 0))
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=85)
        out.append(buf.getvalue())
    return out
