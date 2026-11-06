"""Hand sizing: separate width and length scales for the Paraglider hand, compared against the
uniform knuckle-width scaling e-NABLE uses today, and checked against ANSUR II."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from .hand import PALM_BASE

DATA = Path(__file__).resolve().parent / "data"

PADDING_PER_SIDE = 2.5  # foam inside the palm; 5 mm total matches e-NABLE's "+5 mm" convention
KNUCKLE_ALLOWANCE = 5.0  # e-NABLE convention: measured width + 5 mm
RATIO_LIMITS = (0.88, 1.12)  # width/length scale ratio the joints were checked at
SCALE_LIMITS = (1.0, 2.0)  # Paraglider customizer range


@lru_cache(maxsize=1)
def ansur() -> dict:
    return json.loads((DATA / "ansur_hands.json").read_text())


def percentile(value: float, sex: str, col: str) -> float:
    arr = np.asarray(ansur()[sex][col])
    return float((arr < value).mean() * 100 + (arr == value).mean() * 50)


def ansur_summary() -> dict:
    out = {}
    for sex in ("female", "male"):
        d = ansur()[sex]
        L = np.asarray(d["handlength"], float)
        B = np.asarray(d["handbreadth"], float)
        out[sex] = {
            "n": len(L),
            "hand_length_mean": float(L.mean()),
            "hand_breadth_mean": float(B.mean()),
            "ratio_mean": float((B / L).mean()),
        }
    return out


def _scales_for(breadth: np.ndarray, length: np.ndarray):
    """Vectorized: our separate scales vs uniform knuckle-width scaling. Returns length errors (mm)."""
    B0, L0 = PALM_BASE["knuckle_breadth"], PALM_BASE["hand_length"]
    s_w = (breadth + KNUCKLE_ALLOWANCE) / B0
    s_l = length / L0
    r = np.clip(s_w / s_l, *RATIO_LIMITS)
    s_l_ours = s_w / r
    err_uniform = s_w * L0 - length
    err_ours = s_l_ours * L0 - length
    return err_uniform, err_ours


def population_check() -> dict:
    """Length misfit across ANSUR II when width is fit correctly: uniform scale vs separate scales."""
    out = {"base_ratio": PALM_BASE["knuckle_breadth"] / PALM_BASE["hand_length"], "by_sex": {}}
    for sex in ("female", "male"):
        d = ansur()[sex]
        B = np.asarray(d["handbreadth"], float)
        L = np.asarray(d["handlength"], float)
        eu, eo = _scales_for(B, L)
        hist_bins = np.arange(-10, 36, 2.5)
        out["by_sex"][sex] = {
            "n": int(len(B)),
            "uniform_mean_abs_mm": float(np.abs(eu).mean()),
            "uniform_within_3mm_pct": float((np.abs(eu) <= 3).mean() * 100),
            "ours_mean_abs_mm": float(np.abs(eo).mean()),
            "ours_within_3mm_pct": float((np.abs(eo) <= 3).mean() * 100),
            "uniform_hist": np.histogram(eu, hist_bins)[0].tolist(),
            "ours_hist": np.histogram(eo, hist_bins)[0].tolist(),
            "hist_bins": hist_bins.tolist(),
        }
    return out


def compute_sizing(
    affected_wrist_width: float,
    affected_palm_width: Optional[float],
    sound_hand_length: Optional[float],
    sound_hand_breadth: Optional[float],
    sex: Optional[str],
    age: Optional[float],
) -> dict:
    B0, L0, WI0 = PALM_BASE["knuckle_breadth"], PALM_BASE["hand_length"], PALM_BASE["wrist_inside_width"]
    notes: list[str] = []
    flags: list[str] = []

    # Width: the palm's wrist joint (and palm) must clear the affected side with padding.
    s_fit = (affected_wrist_width + 2 * PADDING_PER_SIDE) / WI0
    if affected_palm_width:
        s_fit = max(s_fit, (affected_palm_width + 2 * PADDING_PER_SIDE) / PALM_BASE["palm_inside_width"])
    s_look = (sound_hand_breadth + KNUCKLE_ALLOWANCE) / B0 if sound_hand_breadth else None
    s_w = max(s_fit, s_look or 0.0)
    if s_look and s_fit > s_look + 0.02:
        notes.append("Width is set by what the palm must fit over on the affected side, not by the sound hand.")

    # Length: match the sound hand; otherwise estimate it from ANSUR II proportions.
    pool = sex if sex in ("female", "male") else None
    if sound_hand_length:
        L_target = sound_hand_length
        length_source = "sound hand"
    else:
        ratios = []
        for sx in ([pool] if pool else ["female", "male"]):
            d = ansur()[sx]
            ratios += list(np.asarray(d["handbreadth"], float) / np.asarray(d["handlength"], float))
        ratio = float(np.median(ratios))
        breadth = sound_hand_breadth or (s_w * B0 - KNUCKLE_ALLOWANCE)
        L_target = breadth / ratio
        length_source = f"estimated from breadth using the ANSUR II median breadth/length ({ratio:.3f})"
        notes.append("No sound-hand length was measured, so length comes from typical proportions.")
    s_l = L_target / L0
    ratio = s_w / s_l
    if not (RATIO_LIMITS[0] <= ratio <= RATIO_LIMITS[1]):
        clamped = float(np.clip(ratio, *RATIO_LIMITS))
        s_l = s_w / clamped
        flags.append(
            f"Width and length differ more than the joints were checked for, so length was limited (ratio {ratio:.2f} -> {clamped:.2f})."
        )
        ratio = clamped
    if s_l < SCALE_LIMITS[0] or s_w < SCALE_LIMITS[0]:
        flags.append("This is below the 100% size the Paraglider design is tested at; consider a smaller design or test-print first.")
    if s_l > SCALE_LIMITS[1] or s_w > SCALE_LIMITS[1]:
        flags.append("This is above the 200% size the design supports.")

    # e-NABLE-style uniform scale from knuckle width, for comparison.
    s_u = s_look if s_look else s_w
    uniform = {"scale": s_u, "length_mm": s_u * L0, "breadth_mm": s_u * B0}
    ours = {"width_scale": s_w, "length_scale": s_l, "length_mm": s_l * L0, "breadth_mm": s_w * B0}
    comparison = {
        "target_length_mm": L_target,
        "uniform_length_error_mm": uniform["length_mm"] - L_target,
        "ours_length_error_mm": ours["length_mm"] - L_target,
    }

    ansur_check = None
    if sound_hand_length and sound_hand_breadth and (age is None or age >= 17):
        ansur_check = {}
        for sx in ("female", "male"):
            ansur_check[sx] = {
                "hand_length_pct": percentile(sound_hand_length, sx, "handlength"),
                "hand_breadth_pct": percentile(sound_hand_breadth, sx, "handbreadth"),
            }
        ref = pool or "female"
        pl, pb = ansur_check[ref]["hand_length_pct"], ansur_check[ref]["hand_breadth_pct"]
        if pl < 5 or pl > 95 or pb < 5 or pb > 95:
            flags.append(
                f"The sound hand is outside the 5th–95th percentile of ANSUR II {ref}s "
                f"(length {pl:.0f}th, breadth {pb:.0f}th). Double-check the photo before printing."
            )
    elif age is not None and age < 17:
        notes.append("ANSUR II covers adults only, so the population check is skipped for children.")

    return {
        "width_scale": round(s_w, 3),
        "length_scale": round(s_l, 3),
        "width_ratio": round(ratio, 3),
        "fit_width_scale": round(s_fit, 3),
        "look_width_scale": round(s_look, 3) if s_look else None,
        "length_source": length_source,
        "uniform": uniform,
        "ours": ours,
        "comparison": comparison,
        "ansur": ansur_check,
        "notes": notes,
        "flags": flags,
        "padding_per_side_mm": PADDING_PER_SIDE,
    }
