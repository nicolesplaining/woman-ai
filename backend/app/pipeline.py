"""End-to-end job: inputs -> checks -> limb model -> sizing/plan -> design -> parts -> printability -> build packet."""
from __future__ import annotations

import io
import json
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import trimesh

from . import ai
from .design import FILAMENT_BY_ID, MOTIFS, _svg_polys, pattern_builder, preview_swatch
from .gauntlet import GauntletParams, build_gauntlet
from .hand import PALM_BASE, render_hand
from .lidar import ScanError, build_limb, guess_orientation, load_scan, scan_views
from .limb import LimbModel, polygon_perimeter, superellipse_contour
from .photo import PhotoError, load_photo, overlay, rectify, refine_wrist, segment_arm, smooth_profile, width_profile
from .printability import Part, check, on_bed, orient_gauntlet, pack, write_3mf
from .sizing import compute_sizing

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "jobs"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

STEPS = {
    "photo": [
        ("inputs", "Read and straighten the photos"),
        ("check", "Check the photos with Claude"),
        ("measure", "Measure the arm"),
        ("plan", "Size the hand and plan the device"),
        ("design", "Design the look"),
        ("generate", "Generate the parts"),
        ("print", "Check printability"),
        ("packet", "Write the build guide"),
    ],
    "lidar": [
        ("inputs", "Read and clean up the scan"),
        ("check", "Check the scan with Claude"),
        ("measure", "Slice the scan into a limb model"),
        ("plan", "Size the hand and plan the device"),
        ("design", "Design the look"),
        ("generate", "Generate the parts"),
        ("print", "Check printability"),
        ("packet", "Write the build guide"),
    ],
}

DEFAULT_DESIGN = {
    "palm_color": "white",
    "fingers_color": "white",
    "gauntlet_color": "sky",
    "accent_color": "white",
    "motif": "none",
    "custom_svg": "",
    "size_mm": 14,
    "spacing_mm": 8,
    "pattern_style": "raised",
    "name_text": "",
    "summary": "A clean, classic look: white hand with a sky-blue gauntlet.",
    "ip_note": "",
}


class InputError(Exception):
    def __init__(self, message: str, retake: Optional[str] = None, photo: Optional[str] = None):
        super().__init__(message)
        self.retake = retake
        self.photo = photo


@dataclass
class Inputs:
    mode: str
    photos: dict = field(default_factory=dict)  # top / side / sound -> bytes
    scan: Optional[tuple[bytes, str]] = None
    refs: list = field(default_factory=list)  # (bytes, media type)
    recipient: dict = field(default_factory=dict)
    tape: dict = field(default_factory=dict)
    manual: dict = field(default_factory=dict)
    design_text: str = ""
    bed: tuple = (220.0, 220.0, 250.0)


class Job:
    def __init__(self, mode: str):
        self.id = uuid.uuid4().hex[:12]
        self.dir = DATA_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.created = time.time()
        self.status = "queued"
        self.steps = [{"key": k, "label": l, "status": "pending", "detail": "", "ai": "", "ms": 0} for k, l in STEPS[mode]]
        self.result: dict = {}
        self.error: Optional[dict] = None
        self.approved: Optional[dict] = None
        self.lock = threading.Lock()

    def view(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "mode": self.mode,
                "status": self.status,
                "steps": [dict(s) for s in self.steps],
                "result": self.result if self.status == "done" else None,
                "error": self.error,
                "approved": self.approved,
            }

    def _step(self, key: str) -> dict:
        return next(s for s in self.steps if s["key"] == key)

    def note(self, key: str, detail: str = "", ai_note: str = "", status: Optional[str] = None):
        with self.lock:
            s = self._step(key)
            if detail:
                s["detail"] = detail
            if ai_note:
                s["ai"] = ai_note
            if status:
                s["status"] = status

    @contextmanager
    def step(self, key: str):
        t0 = time.time()
        with self.lock:
            self._step(key)["status"] = "running"
        try:
            yield
        except Exception:
            with self.lock:
                self._step(key)["status"] = "error"
            raise
        with self.lock:
            s = self._step(key)
            s["ms"] = int((time.time() - t0) * 1000)
            if s["status"] == "running":
                s["status"] = "done"

    def url(self, name: str) -> str:
        return f"/api/jobs/{self.id}/files/{name}"


JOBS: dict[str, Job] = {}
EXECUTOR = ThreadPoolExecutor(max_workers=2)
AI_POOL = ThreadPoolExecutor(max_workers=4)  # Claude calls that can overlap within a job


def submit(inp: Inputs) -> Job:
    job = Job(inp.mode)
    JOBS[job.id] = job
    EXECUTOR.submit(run, job, inp)
    return job


# ---------------------------------------------------------------- helpers

def _depth_ratio(z: np.ndarray) -> np.ndarray:
    """Typical forearm depth/width when there's no side photo."""
    return np.interp(z, [-60, 0, 100, 200], [0.45, 0.66, 0.78, 0.84])


def _contiguous(valid: np.ndarray, start: int) -> tuple[int, int]:
    """Largest run of True containing (or nearest after) index start."""
    n = len(valid)
    if n == 0:
        return 0, 0
    i = start
    while i < n and not valid[i]:
        i += 1
    if i >= n:
        return 0, 0
    a = i
    while a > 0 and valid[a - 1]:
        a -= 1
    b = i
    while b < n - 1 and valid[b + 1]:
        b += 1
    return a, b


def _perspective(W_app, D_app, H_top, H_side, have_side: bool):
    W, D = W_app.copy(), D_app.copy()
    for _ in range(4):
        W = W_app * (H_top - D / 2) / H_top
        if have_side:
            D = D_app * (H_side - W / 2) / H_side
    return W, D


def _limb_from_profiles(z, W, D) -> LimbModel:
    C = np.stack([superellipse_contour(0, 0, w / 2, d / 2, 180, 2.3) for w, d in zip(W, D)])
    return LimbModel(z=z, contours=C, source="photo", uncertainty_mm=2.5)


# ---------------------------------------------------------------- photo mode

PHOTO_DESC = {
    "top": "affected arm from above: forearm and hand flat on the sheet, palm down",
    "side": "affected arm from the side: resting on its side, thumb up",
    "sound": "sound (other) hand from above: flat on the sheet, palm down, fingers together",
}


def photo_measure(job: Job, inp: Inputs) -> tuple[LimbModel, dict, dict]:
    recipient = inp.recipient
    rects, masks, profs, warns = {}, {}, {}, []
    with job.step("inputs"):
        if "top" not in inp.photos:
            raise InputError("The top-view photo of the affected arm is required.", photo="top")
        for key in ("top", "side", "sound"):
            if key not in inp.photos:
                continue
            try:
                rgb, f35 = load_photo(inp.photos[key])
                r = rectify(rgb, f35)
                m = segment_arm(r)
            except PhotoError as e:
                raise InputError(f"Photo '{key}': {e}", retake=e.retake, photo=key)
            rects[key], masks[key] = r, m
            profs[key] = width_profile(m, "top")
            warns += [f"{key}: {w}" for w in r.warnings]
        job.note(
            "inputs",
            ", ".join(f"{k}: {rects[k].n_markers}/4 markers, camera ~{rects[k].camera_height_mm / 10:.0f} cm up" for k in rects),
        )

    # first-pass wrist guesses so the overlays show something useful
    guess = {}
    for k, p in profs.items():
        try:
            guess[k] = refine_wrist(p, None)[0]
        except PhotoError:
            guess[k] = None
    if "side" in profs and guess.get("top") is not None:
        guess["side"] = guess["top"]

    check = {}
    with job.step("check"):
        ov = {k: overlay(rects[k], masks[k], profs[k], f"Photo {k}", guess.get(k)) for k in profs}
        for k, data in ov.items():
            (job.dir / f"overlay_{k}.jpg").write_bytes(data)
        try:
            check = ai.inspect_photos(
                [(k, PHOTO_DESC[k], ov[k]) for k in ("top", "side", "sound") if k in ov],
                {
                    "affected_side": recipient.get("side"),
                    "age": recipient.get("age"),
                    "markers_found": {k: rects[k].n_markers for k in rects},
                    "sharpness": {k: round(rects[k].blur) for k in rects},
                },
            )
            bad = [p for p in check.get("photos", []) if not p.get("usable", True) or not p.get("view_matches", True)]
            if bad:
                b = bad[0]
                raise InputError(
                    f"Photo '{b['photo']}' can't be used: {'; '.join(b.get('issues') or ['it does not show the expected view'])}",
                    retake=b.get("retake_instructions") or "Retake the photo following the guide.",
                    photo=b["photo"],
                )
            job.note("check", ai_note=check.get("summary", ""))
        except ai.AIError as e:
            job.note("check", detail=f"Claude unavailable ({e}); using automatic landmarks.", status="warn")
    by = {p.get("photo"): p for p in check.get("photos", [])}

    with job.step("measure"):
        for k in list(profs):
            if by.get(k, {}).get("hand_end") == "bottom":
                profs[k] = width_profile(masks[k], "bottom")
        cl_top = by.get("top", {}).get("wrist_tick_mm", -1)
        wrist_top, how = refine_wrist(profs["top"], cl_top if cl_top is not None and cl_top >= 0 else None)
        pt = profs["top"]
        if not pt.valid[: max(int(wrist_top) - 5, 1)].any():
            warns.append("The hand end isn't fully on the sheet in the top photo; residual-hand size is estimated.")
        have_side = "side" in profs
        if have_side:
            ps = profs["side"]
            cl_side = by.get("side", {}).get("wrist_tick_mm", -1)
            wrist_side = wrist_top
            if cl_side is not None and cl_side >= 0 and abs(cl_side - wrist_top) > 20:
                wrist_side = float(cl_side)
                warns.append("The side photo's wrist didn't line up with the top photo; used Claude's reading of the side photo.")
            if not ps.valid[:3].any() and cl_side is not None and cl_side >= 0:
                wrist_side = float(cl_side)

        sm_top = smooth_profile(pt)
        z_lo = -wrist_top
        z_hi_top = pt.s[-1] - wrist_top
        z = np.arange(np.ceil(z_lo), np.floor(z_hi_top), 1.0)
        W_app = np.interp(z + wrist_top, pt.s, sm_top)
        v_top = np.interp(z + wrist_top, pt.s, pt.valid.astype(float)) > 0.5
        if have_side:
            sm_side = smooth_profile(ps)
            D_app = np.interp(z + wrist_side, ps.s, sm_side, left=np.nan, right=np.nan)
            v_side = np.interp(z + wrist_side, ps.s, ps.valid.astype(float), left=0, right=0) > 0.5
        else:
            D_app = W_app * _depth_ratio(z)
            v_side = np.ones_like(v_top)
            warns.append("No side photo, so forearm depth is estimated from typical proportions. Add a side photo for a better fit.")
        valid = v_top & v_side & ~np.isnan(D_app)
        i0 = int(np.argmin(np.abs(z)))
        a, b = _contiguous(valid, i0)
        if b - a < 60 or z[b] < 70:
            raise InputError(
                "Not enough of the forearm is measurable on the sheet.",
                retake="Lay the arm along the long side of the sheet with the wrist crease near the top edge, so most of the forearm is on the paper.",
                photo="top",
            )
        # keep the hand part (z < 0) only where it's valid too
        z, W_app, D_app = z[a : b + 1], W_app[a : b + 1], D_app[a : b + 1]
        H_top = rects["top"].camera_height_mm
        H_side = rects["side"].camera_height_mm if have_side else H_top
        W, D = _perspective(W_app, D_app, H_top, H_side, have_side)
        persp = float(np.mean(W / W_app))

        limb = _limb_from_profiles(z, W, D)
        # tape measurements pin the circumference
        tape_notes = []
        wc = float(inp.tape.get("wrist_circ") or 0)
        fc = float(inp.tape.get("forearm_circ") or 0)
        k_pts = []
        if wc > 0:
            k_pts.append((0.0, wc / limb.circumference(0.0)))
        if fc > 0:
            zs = z[z > 20]
            circ = np.array([limb.circumference(q) for q in zs[::5]])
            zmax = float(zs[::5][np.argmax(circ)])
            k_pts.append((zmax, fc / circ.max()))
        if k_pts:
            ks = [k for _, k in k_pts]
            if any(abs(k - 1) > 0.15 for k in ks):
                warns.append("The tape measurements differ from the photos by more than 15%; check them. Using the photos.")
            else:
                if len(k_pts) == 1:
                    kz = np.full_like(z, ks[0])
                else:
                    (z1, k1), (z2, k2) = sorted(k_pts)
                    kz = np.interp(z, [z1, z2], [k1, k2])
                W, D = W * kz, D * kz
                limb = _limb_from_profiles(z, W, D)
                limb.uncertainty_mm = 1.5
                tape_notes.append(f"Scaled to tape measurements (x{np.mean(kz):.3f}).")

        meas = _limb_measurements(limb)
        distal = z < -8
        if distal.any():
            meas["residual_palm_width"] = round(float(W[distal & (z > -90)].max()), 1) if (distal & (z > -90)).any() else None
            meas["residual_length"] = round(float(-z[0]), 1)
        meas["camera_height_cm"] = {k: round(rects[k].camera_height_mm / 10) for k in rects}
        meas["perspective_correction"] = round(persp, 4)
        meas["wrist_method"] = how

        sound = {}
        if "sound" in profs:
            psd = profs["sound"]
            cl = by.get("sound", {})
            cw = cl.get("wrist_tick_mm", -1)
            ws_sound, _ = refine_wrist(psd, cw if cw is not None and cw >= 0 else None)
            if not psd.valid[:3].any():
                warns.append("The fingertips aren't fully on the sheet in the sound-hand photo; hand length may be short.")
            L = ws_sound
            ck = cl.get("knuckle_tick_mm", -1)
            k_s = float(ck) if ck is not None and ck >= 0 and 0.30 * L <= ws_sound - ck <= 0.68 * L else ws_sound - 0.59 * L
            # widest point within +-12 mm of the knuckle line (fingers together, thumb against the side)
            spr = smooth_profile(psd)
            win = (np.abs(psd.s - k_s) <= 12) & psd.valid
            breadth_app = float(spr[win].max()) if win.any() else float(np.interp(k_s, psd.s, spr))
            Hs = rects["sound"].camera_height_mm
            breadth = breadth_app * (Hs - 14.0) / Hs
            length = L * (Hs - 3.0) / Hs
            sound = {"hand_length": round(length, 1), "hand_breadth": round(breadth, 1)}
        if inp.manual.get("sound_length"):
            sound["hand_length"] = float(inp.manual["sound_length"])
        if inp.manual.get("sound_breadth"):
            sound["hand_breadth"] = float(inp.manual["sound_breadth"])
        meas["sound_hand"] = sound
        meas["uncertainty_mm"] = limb.uncertainty_mm
        job.note(
            "measure",
            f"Wrist {meas['wrist_width']:.1f} x {meas['wrist_depth']:.1f} mm (circumference {meas['wrist_circumference']:.0f} mm); "
            f"{meas['forearm_length_measured']:.0f} mm of forearm measured" + (f"; sound hand {sound.get('hand_length')} x {sound.get('hand_breadth')} mm" if sound else "")
            + ("; " + " ".join(tape_notes) if tape_notes else ""),
        )
        # final overlays with the chosen wrist
        for k in profs:
            wsel = {"top": wrist_top, "side": wrist_side if have_side else None, "sound": ws_sound if "sound" in profs else None}.get(k)
            (job.dir / f"overlay_{k}.jpg").write_bytes(overlay(rects[k], masks[k], profs[k], f"Photo {k}", wsel))
    return limb, meas, {"check": check, "warnings": warns}


def _limb_measurements(limb: LimbModel) -> dict:
    w0, d0 = limb.width_depth(0.0)
    zs = limb.z[limb.z >= 0]
    circ = [limb.circumference(q) for q in zs[::4]]
    out = {
        "wrist_width": round(w0, 1),
        "wrist_depth": round(d0, 1),
        "wrist_circumference": round(limb.circumference(0.0), 1),
        "forearm_max_circumference": round(float(max(circ)), 1),
        "forearm_length_measured": round(float(limb.z[-1]), 1),
        "profile": [
            {"z": float(q), "width": round(limb.width_depth(q)[0], 1), "depth": round(limb.width_depth(q)[1], 1), "circumference": round(limb.circumference(q), 1)}
            for q in np.arange(np.ceil(limb.z[0] / 10) * 10, limb.z[-1], 10)
        ],
    }
    return out


# ---------------------------------------------------------------- lidar mode

def lidar_measure(job: Job, inp: Inputs) -> tuple[LimbModel, dict, dict]:
    warns = []
    with job.step("inputs"):
        if not inp.scan:
            raise InputError("Upload a scan file (OBJ, STL, PLY or GLB).")
        try:
            mesh, notes = load_scan(*inp.scan)
        except ScanError as e:
            raise InputError(str(e), retake=e.retake)
        views = scan_views(mesh)
        for i, data in enumerate(views.images):
            (job.dir / f"scan_view_{i + 1}.jpg").write_bytes(data)
        job.note("inputs", f"{len(mesh.faces):,} triangles. " + " ".join(notes))
    check = {}
    with job.step("check"):
        try:
            check = ai.inspect_scan(views.images, {"affected_side": inp.recipient.get("side"), "age": inp.recipient.get("age")})
            if not check.get("scan_ok", True):
                warns += check.get("issues", [])
            job.note("check", ai_note=check.get("summary", ""))
            orient = {"hand_end": check["hand_end"], "wrist_tick_mm": check["wrist_tick_mm"], "dorsal_view": check["dorsal_view"]}
        except (ai.AIError, KeyError) as e:
            orient = guess_orientation(mesh, views)
            job.note("check", detail=f"Claude unavailable ({e}); guessed the orientation. Check that the opening is on the palm side.", status="warn")
    with job.step("measure"):
        try:
            limb = build_limb(mesh, views, orient["hand_end"], float(orient["wrist_tick_mm"]), int(orient["dorsal_view"]))
        except ScanError as e:
            raise InputError(str(e), retake=e.retake)
        # snap the wrist to the narrowest section near the estimate
        zs = limb.z[(limb.z > -25) & (limb.z < 25)]
        if len(zs) > 5:
            widths = np.array([limb.width_depth(q)[0] for q in zs])
            shift = float(zs[np.argmin(widths)])
            if abs(shift) > 0.5:
                limb.z = limb.z - shift
        meas = _limb_measurements(limb)
        distal = limb.z[(limb.z < -8) & (limb.z > -90)]
        if len(distal):
            meas["residual_palm_width"] = round(max(limb.width_depth(q)[0] for q in distal), 1)
            meas["residual_length"] = round(float(-limb.z[0]), 1)
        sound = {}
        if "sound" in inp.photos:
            try:
                rgb, f35 = load_photo(inp.photos["sound"])
                r = rectify(rgb, f35)
                m = segment_arm(r)
                p = width_profile(m, "top")
                ws, _ = refine_wrist(p, None)
                Hs = r.camera_height_mm
                sound = {
                    "hand_length": round(ws * (Hs - 3) / Hs, 1),
                    "hand_breadth": round(float(np.interp(ws - 0.59 * ws, p.s, smooth_profile(p))) * (Hs - 14) / Hs, 1),
                }
                (job.dir / "overlay_sound.jpg").write_bytes(overlay(r, m, p, "Photo sound", ws))
            except PhotoError as e:
                warns.append(f"Sound-hand photo skipped: {e}")
        if inp.manual.get("sound_length"):
            sound["hand_length"] = float(inp.manual["sound_length"])
        if inp.manual.get("sound_breadth"):
            sound["hand_breadth"] = float(inp.manual["sound_breadth"])
        meas["sound_hand"] = sound
        meas["uncertainty_mm"] = limb.uncertainty_mm
        job.note("measure", f"Wrist {meas['wrist_width']:.1f} x {meas['wrist_depth']:.1f} mm; {meas['forearm_length_measured']:.0f} mm of forearm in the scan")
    return limb, meas, {"check": check, "warnings": warns}


# ---------------------------------------------------------------- main run

def run(job: Job, inp: Inputs):
    with job.lock:
        job.status = "running"
    t0 = time.time()
    try:
        _run(job, inp)
        with job.lock:
            job.status = "done"
            job.result["seconds"] = round(time.time() - t0, 1)
    except InputError as e:
        with job.lock:
            job.status = "error"
            job.error = {"message": str(e), "retake": e.retake, "photo": e.photo}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with job.lock:
            job.status = "error"
            job.error = {"message": f"Something went wrong: {e}", "retake": None, "photo": None}


def _run(job: Job, inp: Inputs):
    rec = inp.recipient
    age = float(rec["age"]) if rec.get("age") not in (None, "") else None
    if inp.mode == "photo":
        limb, meas, extra = photo_measure(job, inp)
    else:
        limb, meas, extra = lidar_measure(job, inp)
    warnings = list(extra["warnings"])
    limb_chk = extra["check"]

    # the design call doesn't depend on the plan, so start it now
    design_future = None
    if inp.design_text.strip() or inp.refs:
        design_future = AI_POOL.submit(ai.design_spec, inp.design_text, inp.refs, rec)

    # -------- plan
    with job.step("plan"):
        sound = meas.get("sound_hand", {})
        sizing = compute_sizing(
            affected_wrist_width=meas["wrist_width"],
            affected_palm_width=meas.get("residual_palm_width"),
            sound_hand_length=sound.get("hand_length"),
            sound_hand_breadth=sound.get("hand_breadth"),
            sex=rec.get("sex"),
            age=age,
        )
        gap_lo, gap_hi = (2.0, 6.0) if inp.mode == "photo" else (1.5, 5.0)
        default_gap = (4.0 if inp.mode == "photo" else 2.5) + (1.0 if age is not None and age < 14 else 0.0)
        default_gap = float(np.clip(default_gap, gap_lo, gap_hi))
        avail = float(meas["forearm_length_measured"]) - 18.0 - 3.0
        len_hi = float(min(150.0, avail))
        len_lo = float(min(60.0, len_hi))
        default_len = float(np.clip(75.0 * sizing["length_scale"], len_lo, len_hi))
        bounds = {"gap_mm": [gap_lo, gap_hi], "gauntlet_length_mm": [round(len_lo), round(len_hi)]}
        plan = {
            "device": "paraglider_wrist_powered",
            "suitability_rationale": "",
            "gap_mm": default_gap,
            "gauntlet_length_mm": default_len,
            "sizing_explanation": "",
            "warnings": [],
            "volunteer_checks": [],
        }
        limb_check = {k: limb_chk.get(k) for k in ("limb_difference", "limb_difference_notes", "suitable_for_wrist_powered") if limb_chk.get(k)}
        try:
            got = ai.plan_device(
                {k: v for k, v in meas.items() if k != "profile"},
                {k: sizing[k] for k in ("width_scale", "length_scale", "width_ratio", "length_source", "comparison", "ansur", "notes", "flags")},
                {k: rec.get(k) for k in ("age", "sex", "side", "goals")},
                limb_check,
                bounds,
            )
            plan.update(got)
            job.note("plan", ai_note=got.get("sizing_explanation", ""))
        except ai.AIError as e:
            job.note("plan", detail=f"Claude unavailable ({e}); used default padding and length.", status="warn")
        plan["gap_mm"] = float(np.clip(plan["gap_mm"], gap_lo, gap_hi))
        plan["gauntlet_length_mm"] = float(np.clip(plan["gauntlet_length_mm"], len_lo, len_hi))
        job.note(
            "plan",
            f"Width {sizing['width_scale'] * 100:.0f}%, length {sizing['length_scale'] * 100:.0f}%; padding gap {plan['gap_mm']:.1f} mm; gauntlet {plan['gauntlet_length_mm']:.0f} mm",
        )
        if plan["device"] != "paraglider_wrist_powered":
            warnings.append("Claude suggests a volunteer review whether a wrist-powered hand is right for this recipient: " + plan.get("suitability_rationale", ""))

    # -------- design
    with job.step("design"):
        spec = dict(DEFAULT_DESIGN)
        if design_future is not None:
            try:
                got = design_future.result()
                spec.update(got)
                job.note("design", ai_note=got.get("summary", ""))
            except ai.AIError as e:
                job.note("design", detail=f"Claude unavailable ({e}); using the default look.", status="warn")
        for k in ("palm_color", "fingers_color", "gauntlet_color", "accent_color"):
            if spec.get(k) not in FILAMENT_BY_ID:
                spec[k] = DEFAULT_DESIGN[k]
        if spec.get("motif") not in MOTIFS and spec.get("motif") != "custom":
            spec["motif"] = "none"
        if spec.get("motif") == "custom":
            try:
                _svg_polys(spec.get("custom_svg") or "", 40)
            except Exception:
                spec["motif"] = "stars"
                warnings.append("Claude's custom motif couldn't be read, so stars were used instead.")
        spec["size_mm"] = float(np.clip(spec.get("size_mm") or 14, 8, 24))
        spec["spacing_mm"] = float(np.clip(spec.get("spacing_mm") or 8, 4, 16))
        (job.dir / "design_swatch.png").write_bytes(preview_swatch(spec))
        if not job._step("design")["detail"]:
            job.note("design", f"{FILAMENT_BY_ID[spec['gauntlet_color']]['name']} gauntlet, {MOTIFS.get(spec['motif'], 'custom motif').lower()}" + (f", “{spec['name_text']}”" if spec.get("name_text") else ""))

    # -------- generate
    with job.step("generate"):
        right = (rec.get("side") or "left").lower() == "right"
        hand = render_hand(job.dir / "hand", sizing["length_scale"], sizing["width_ratio"], right)
        has_pattern = spec["motif"] != "none" or bool(spec.get("name_text"))
        height = 0.8 if spec.get("pattern_style") != "recessed" else -0.8
        params = GauntletParams(
            width_scale=sizing["width_scale"],
            length_scale=sizing["length_scale"],
            gap_mm=plan["gap_mm"],
            length_mm=plan["gauntlet_length_mm"],
            pattern_height_mm=height if has_pattern else 0.0,
        )
        g = build_gauntlet(limb, params, track_plate_mesh=hand["track"], pattern_builder=pattern_builder(spec) if has_pattern else None)
        warnings += g.info["warnings"]
        arm_mesh = limb.to_trimesh()
        arm_mesh.export(job.dir / "view_arm.stl")
        g.gauntlet.export(job.dir / "view_gauntlet.stl")
        g.fit_ring.export(job.dir / "view_fit_ring.stl")
        try:
            from .hand import assemble_preview

            asm = assemble_preview(job.dir / "hand", sizing["length_scale"], sizing["width_ratio"], right, g.info["hinge"]["center"])
            for name, mesh in asm.items():
                mesh.export(job.dir / f"view_{name}.stl")
        except Exception:  # preview only; never block the kit
            traceback.print_exc()
        job.note("generate", f"Hand parts rendered by OpenSCAD in {hand['render_ms'] / 1000:.1f} s; gauntlet {g.info['length_mm']:.0f} mm long, {len(g.gauntlet.faces):,} triangles")

    # -------- printability
    with job.step("print"):
        col = lambda cid: (FILAMENT_BY_ID[cid]["hex"], FILAMENT_BY_ID[cid]["name"])  # noqa: E731
        color_for = {
            "palm": spec["palm_color"],
            "long_finger": spec["fingers_color"],
            "short_finger": spec["fingers_color"],
            "thumb": spec["fingers_color"],
            "finger_phalanx": spec["accent_color"],
            "thumb_phalanx": spec["accent_color"],
            "tensioner": spec["accent_color"],
        }
        parts: list[Part] = []
        for pid, info in hand["parts"].items():
            m = trimesh.load(info["path"], force="mesh")
            hx, hn = col(color_for[pid])
            parts.append(Part(pid, info["label"], on_bed(m), info["count"], hx, hn))
        gx, gn = col(spec["gauntlet_color"])
        parts.append(Part("gauntlet", "Custom gauntlet", orient_gauntlet(g.gauntlet), 1, gx, gn))
        ring = Part("fit_ring", "Fit-test ring (print first)", orient_gauntlet(g.fit_ring), 1, gx, gn)
        report = [check(p, inp.bed) for p in [ring, *parts]]
        files = []
        for p in [ring, *parts]:
            name = f"{p.id}.stl"
            p.mesh.export(job.dir / name)
            files.append({"name": name, "label": f"{p.label} ×{p.count}", "kind": "stl"})
        plates = pack(parts, inp.bed)
        manifest = []
        for i, plate in enumerate(plates, start=1):
            name = f"plate_{i}.3mf"
            (job.dir / name).write_bytes(write_3mf(plate))
            files.append({"name": name, "label": f"Build plate {i} (3MF, colors set)", "kind": "3mf"})
            manifest.append([{"part": p.id, "file": job.url(f"{p.id}.stl"), "offset": t.tolist(), "color": p.color} for p, t in plate])
        (job.dir / "fit_ring.3mf").write_bytes(write_3mf([(ring, np.zeros(3))]))
        # one file with every part, plates side by side, for "open in slicer" (slicers can re-arrange)
        kit = [(p, t + np.array([i * (inp.bed[0] + 25.0), 0.0, 0.0])) for i, plate in enumerate(plates) for p, t in plate]
        (job.dir / "kit.3mf").write_bytes(write_3mf(kit))
        files.insert(0, {"name": "fit_ring.3mf", "label": "Fit-test ring (3MF) – print this first", "kind": "3mf"})
        files.insert(1, {"name": "kit.3mf", "label": "Whole kit in one file (3MF, colors set)", "kind": "3mf"})
        total_g = sum(r["grams_each"] * r["count"] for r in report)
        total_min = sum(r["minutes_each"] * r["count"] for r in report)
        bad = [r for r in report if r["status"] == "fail"]
        job.note("print", f"{len(plates)} build plate(s); about {total_g:.0f} g of filament and {total_min / 60:.1f} h of printing (rough estimate)", status="warn" if bad else None)

    # -------- packet
    with job.step("packet"):
        summary = {
            "recipient": {k: rec.get(k) for k in ("age", "sex", "side", "goals")},
            "input_mode": inp.mode,
            "measurements": {k: v for k, v in meas.items() if k != "profile"},
            "sizing": {k: sizing[k] for k in ("width_scale", "length_scale", "width_ratio", "comparison", "flags", "notes")},
            "plan": plan,
            "design": {
                **{k: spec[k] for k in ("palm_color", "fingers_color", "gauntlet_color", "accent_color", "motif", "name_text")},
                "pattern_note": "The motif and name are a raised/recessed texture in the gauntlet's own color (one filament per part). Accent color = finger phalanges, thumb phalanx and tensioner.",
            },
            "gauntlet": {k: g.info[k] for k in ("length_mm", "gap_mm", "wall_mm", "opening_deg") if k in g.info},
            "parts": [{k: r[k] for k in ("label", "count", "color_name", "minutes_each", "grams_each", "status", "notes")} for r in report],
            "hardware": "M3 screws for finger joints and wrist hinges, 1.5 mm elastic cord, braided fishing line (80 lb), 5 mm adhesive foam, 1-inch velcro straps x2, two 3x30 mm screws for the tensioner",
            "print_settings": "PLA or PETG, 0.4 mm nozzle, 0.24-0.32 mm layers, 3 walls, 30% infill, no supports or rafts; gauntlet stands on its elbow end",
            "warnings": warnings,
        }
        language = rec.get("language") or "English"
        try:
            packet = ai.build_packet(summary, language)
        except ai.AIError as e:
            packet = {"volunteer_guide_markdown": _fallback_guide(summary), "recipient_summary": "", "recipient_language": language}
            job.note("packet", detail=f"Claude unavailable ({e}); wrote a basic guide.", status="warn")
        (job.dir / "build_guide.md").write_text(packet["volunteer_guide_markdown"])
        files.append({"name": "build_guide.md", "label": "Build guide (Markdown)", "kind": "md"})
        (job.dir / "measurements.json").write_text(json.dumps({"measurements": meas, "sizing": sizing, "plan": plan, "design": spec}, indent=1, default=float))
        files.append({"name": "measurements.json", "label": "Measurements and settings (JSON)", "kind": "json"})
        with zipfile.ZipFile(job.dir / "fitted_package.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(job.dir / f["name"], f["name"])
            z.writestr("CREDITS.txt", CREDITS)
        files.insert(0, {"name": "fitted_package.zip", "label": "Everything (ZIP)", "kind": "zip"})

    overlays = [{"key": k, "url": job.url(f"overlay_{k}.jpg")} for k in ("top", "side", "sound") if (job.dir / f"overlay_{k}.jpg").exists()]
    overlays += [{"key": f"scan view {i}", "url": job.url(f"scan_view_{i}.jpg")} for i in (1, 2) if (job.dir / f"scan_view_{i}.jpg").exists()]
    with job.lock:
        job.result = {
            "mode": inp.mode,
            "recipient": {k: rec.get(k) for k in ("name", "age", "sex", "side", "goals", "language")},
            "measurements": meas,
            "sizing": sizing,
            "plan": plan,
            "check": limb_chk,
            "design": {**spec, "swatch": job.url("design_swatch.png")},
            "gauntlet": {k: v for k, v in g.info.items() if k != "warnings"},
            "printability": report,
            "plates": manifest,
            "files": [{**f, "url": job.url(f["name"])} for f in files],
            "viewer": {
                "arm": job.url("view_arm.stl"),
                "gauntlet": job.url("view_gauntlet.stl"),
                "fit_ring": job.url("view_fit_ring.stl"),
                "hand": [
                    {"url": job.url(f"view_{n}.stl"), "color": FILAMENT_BY_ID[spec[c]]["hex"]}
                    for n, c in (("palm", "palm_color"), ("tips", "fingers_color"), ("phalanges", "accent_color"))
                    if (job.dir / f"view_{n}.stl").exists()
                ],
            },
            "overlays": overlays,
            "packet": packet,
            "warnings": warnings,
            "base": PALM_BASE,
        }


CREDITS = """Hand parts: Paraglider (flexible_flyer) by Marcus Mendenhall, derived from the e-NABLE Phoenix Reborn Hand
(enablesierraleone, Albert Fung), Phoenix Hand v2 and UnLimbited Arm. Licensed CC BY 3.0.
https://github.com/mendenm/flexible_flyer
Gauntlet: generated by Fitted from the recipient's photos or scan; tensioner track from the Paraglider thermo gauntlet.
This is an e-NABLE-style assistive device, not a medical device. A volunteer must check the fit before delivery.
"""


def _fallback_guide(s: dict) -> str:
    rows = "\n".join(f"| {p['label']} | {p['count']} | {p['color_name']} | ~{p['minutes_each']} min |" for p in s["parts"])
    warn = "\n".join(f"- {w}" for w in s["warnings"]) or "- None"
    return f"""## Summary
Paraglider wrist-powered hand at {s['sizing']['width_scale'] * 100:.0f}% width / {s['sizing']['length_scale'] * 100:.0f}% length with a custom-fit gauntlet ({s['gauntlet'].get('length_mm', 0):.0f} mm long, {s['gauntlet'].get('gap_mm', 0):.1f} mm padding gap).

## Print first: fit-test ring
Print `fit_ring.3mf` (about 15 minutes) and check it on the forearm with the foam padding in place before printing anything else.

## Print list
| Part | Qty | Color | Time each |
|---|---|---|---|
{rows}

## Print settings
{s['print_settings']}

## Hardware
{s['hardware']}

## Fit checks before delivery
- Ring sits snugly with padding, no pressure points.
- Wrist bends freely; fingers close fully when the wrist flexes.

## Warnings
{warn}
"""


def sample_inputs(sample: str) -> dict:
    d = SAMPLES_DIR / sample
    if not d.is_dir():
        raise InputError("Unknown sample.")
    return {
        "photos": {k: (d / f).read_bytes() for k, f in (("top", "A_top.jpg"), ("side", "B_side.jpg"), ("sound", "C_sound.jpg")) if (d / f).exists()},
        "scan": ((d / "scan.ply").read_bytes(), "scan.ply") if (d / "scan.ply").exists() else None,
    }
