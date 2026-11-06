"""Accuracy benchmark on synthetic people with known ground truth.

  1. Photo measurement: synthetic arms with ANSUR II proportions (adults and child-sized), varied skin
     tones, camera heights and lenses, rendered on the marker sheet and measured by the real pipeline
     (automatic landmarks, no Claude). Ablations on the same photos: no perspective correction, no side
     photo, and with tape measurements.
  2. LiDAR: noisy synthetic scans with holes in random poses.
  3. Sizing end to end: ANSUR II women and men, device length/breadth error for e-NABLE-style uniform
     scaling vs Fitted's separate scales, including Fitted's own photo measurement error.
  4. (--claude) Bad-photo catch rate: blurred, swapped-view, dark, off-sheet and covered-marker photos.

Synthetic renders are cleaner than real photos, so these numbers are a best case for the method; the
real-person study in PLAN.md is still needed. Writes benchmark/results.json and benchmark/subjects.csv.

Run: .venv/bin/python scripts/benchmark.py [--n 80] [--workers 6] [--claude]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SKIN = {  # roughly Fitzpatrick I-VI
    "I": (236, 198, 176),
    "II": (222, 176, 148),
    "III": (200, 150, 116),
    "IV": (165, 115, 82),
    "V": (122, 82, 58),
    "VI": (82, 56, 42),
}
Z_EVAL = (0, 40, 80)
GAUNTLET_Z = (18.0, 90.0)


# ---------------------------------------------------------------- subjects

def _ansur_rows(rng, n):
    from app.sizing import ansur

    rows = []
    for i in range(n):
        sex = "female" if i % 2 == 0 else "male"
        d = ansur()[sex]
        j = int(rng.integers(len(d["handlength"])))
        rows.append(
            {
                "sex": sex,
                "L": float(d["handlength"][j]),
                "B": float(d["handbreadth"][j]),
                "Wc": float(d["wristcircumference"][j]),
                "Fc": float(d["forearmcircumferenceflexed"][j]),
            }
        )
    return rows


def make_subject(i: int, row: dict, rng) -> dict:
    child = i % 4 in (1, 2)  # half the subjects are child-sized
    k = float(rng.uniform(0.70, 0.88)) if child else 1.0
    return {
        "id": i,
        "sex": row["sex"],
        "child": child,
        "age": int(rng.integers(7, 14)) if child else int(rng.integers(18, 60)),
        "k": k,
        "L": row["L"],
        "B": row["B"],
        "Wc": row["Wc"],
        "Fc": row["Fc"],
        "skin": list(SKIN)[int(rng.integers(len(SKIN)))],
        "height": float(rng.uniform(550, 1100)),
        "f35": float(rng.choice([26.0, 52.0])),
        "seed": 1000 + i,
    }


def build_arms(s: dict):
    from app.synthetic import FOREARM, RESIDUAL, SOUND, arm_model

    k = s["k"]
    lz = s["L"] / 180.0 * k  # length scale
    hw = s["B"] / 78.0 * k  # hand width scale
    ww = s["Wc"] / 154.0 * k  # wrist scale
    fw = s["Fc"] / 264.0 * k  # proximal forearm scale

    def forearm():
        z = np.array(FOREARM["z"], float)
        t = np.clip(z / 160.0, 0, 1)
        sc = ww * (1 - t) + fw * t
        return z * lz, np.array(FOREARM["w"]) * sc, np.array(FOREARM["d"]) * sc

    def hand(tmpl):
        z = np.array(tmpl["z"], float) * lz
        w = np.array(tmpl["w"], float) * hw
        d = np.array(tmpl["d"], float) * hw
        # meet the forearm at the wrist
        w[-1], d[-1] = FOREARM["w"][0] * ww, FOREARM["d"][0] * ww
        return z, w, d

    fz, fwid, fdep = forearm()
    out = {}
    for name, tmpl in (("affected", RESIDUAL), ("sound", SOUND)):
        hz, hwid, hdep = hand(tmpl)
        spec = {"z": list(hz) + list(fz[1:]), "w": list(hwid) + list(fwid[1:]), "d": list(hdep) + list(fdep[1:])}
        # arm_model joins hand[:-1] + forearm; pass the combined profile as the "hand" with an empty-tail trick
        out[name] = _model_from_profile(spec)
    out["sound_truth"] = {"length": 180.0 * lz, "breadth": 78.0 * hw}
    out["residual_length"] = -float(np.array(RESIDUAL["z"]).min()) * lz
    return out


def _model_from_profile(spec):
    from scipy.interpolate import PchipInterpolator

    from app.limb import LimbModel, superellipse_contour

    zs, ws, ds = map(np.asarray, (spec["z"], spec["w"], spec["d"]))
    z = np.arange(zs[0], zs[-1] + 1e-6, 1.0)
    W = PchipInterpolator(zs, ws)(z)
    D = PchipInterpolator(zs, ds)(z)
    C = np.stack([superellipse_contour(0, 0, a / 2, b / 2, 160, 2.3) for a, b in zip(W, D)])
    return LimbModel(z=z, contours=C, source="synthetic", uncertainty_mm=0.0)


def render_set(s: dict, arms: dict) -> dict:
    from app.synthetic import render_photo

    rng = np.random.default_rng(s["seed"])
    skin = SKIN[s["skin"]]
    res_len = arms["residual_length"]
    L = arms["sound_truth"]["length"]
    wy = float(np.clip(res_len + rng.uniform(5, 20), 20, 95))
    sy = float(np.clip(L + rng.uniform(8, 22), 60, 238))
    kw = dict(f35=s["f35"], height=s["height"], skin=skin)
    return {
        "top": render_photo(arms["affected"], "top", wrist_xy=(95 + rng.uniform(-8, 8), wy), seed=s["seed"], **kw),
        "side": render_photo(arms["affected"], "side", wrist_xy=(97 + rng.uniform(-8, 8), wy + rng.uniform(-4, 4)), seed=s["seed"] + 1, **kw),
        "sound": render_photo(arms["sound"], "top", wrist_xy=(93 + rng.uniform(-8, 8), sy), seed=s["seed"] + 2, **kw),
    }


# ---------------------------------------------------------------- scoring

def surface_errors(meas, truth) -> np.ndarray:
    from app.limb import resample_polar

    z = np.arange(GAUNTLET_Z[0], min(GAUNTLET_Z[1], meas.z[-1], truth.z[-1]), 2.0)
    a = meas.sections_at(z)
    b = truth.sections_at(z)
    errs = []
    for ca, cb in zip(a, b):
        ra = np.linalg.norm(resample_polar(ca, 180, np.zeros(2)), axis=1)
        rb = np.linalg.norm(resample_polar(cb, 180, np.zeros(2)), axis=1)
        errs.append(ra - rb)
    return np.concatenate(errs)


def score(meas_limb, meas: dict, arms: dict) -> dict:
    truth = arms["affected"]
    out = {}
    for z in Z_EVAL:
        w_m, d_m = meas_limb.width_depth(z)
        w_t, d_t = truth.width_depth(z)
        out[f"width_{z}"] = w_m - w_t
        out[f"depth_{z}"] = d_m - d_t
        out[f"circ_{z}"] = meas_limb.circumference(z) - truth.circumference(z)
    se = surface_errors(meas_limb, truth)
    out["surface_mae"] = float(np.abs(se).mean())
    out["surface_p95"] = float(np.percentile(np.abs(se), 95))
    out["surface_within_2mm"] = float((np.abs(se) <= 2).mean() * 100)
    snd = meas.get("sound_hand") or {}
    if snd.get("hand_length"):
        out["sound_length"] = snd["hand_length"] - arms["sound_truth"]["length"]
        out["sound_breadth"] = snd["hand_breadth"] - arms["sound_truth"]["breadth"]
    return out


# ---------------------------------------------------------------- workers

def _worker_init(tmp: str):
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    from app import ai, pipeline

    ai.available = lambda: False  # automatic landmarks only
    pipeline.DATA_DIR = Path(tmp)


def run_photo_subject(s: dict) -> dict:
    from app import pipeline

    arms = build_arms(s)
    photos = render_set(s, arms)
    recipient = {"age": s["age"], "sex": s["sex"], "side": "left"}
    variants = {
        "full": dict(photos=photos, tape={}),
        "no_side": dict(photos={k: v for k, v in photos.items() if k != "side"}, tape={}),
        "tape": dict(
            photos=photos,
            tape={
                "wrist_circ": arms["affected"].circumference(0.0) + np.random.default_rng(s["seed"] + 9).normal(0, 1.5),
                "forearm_circ": None,
            },
        ),
        "no_perspective": dict(photos=photos, tape={}, no_persp=True),
    }
    res = {"subject": s}
    orig = pipeline._perspective
    for name, v in variants.items():
        if v.get("no_persp"):
            pipeline._perspective = lambda W, D, *a: (W.copy(), D.copy())
        try:
            job = pipeline.Job("photo")
            inp = pipeline.Inputs(mode="photo", photos=v["photos"], recipient=recipient, tape=v["tape"])
            limb, meas, _ = pipeline.photo_measure(job, inp)
            res[name] = {"ok": True, **score(limb, meas, arms)}
        except Exception as e:  # noqa: BLE001
            res[name] = {"ok": False, "error": str(e)[:160]}
        finally:
            pipeline._perspective = orig
    return res


def run_lidar_subject(s: dict) -> dict:
    from app import lidar
    from app.synthetic import lidar_scan

    arms = build_arms(s)
    scan = lidar_scan(arms["affected"], seed=s["seed"])
    buf = io.BytesIO()
    scan.export(buf, file_type="ply")
    data = buf.getvalue()
    try:
        if hasattr(lidar, "load_scan"):
            m, _ = lidar.load_scan(data, "scan.ply")
        else:
            raw, _ = lidar.load_raw(data, "scan.ply")
            m, _ = lidar.finalize_scan(raw)
        v = lidar.scan_views(m)
        g = lidar.guess_orientation(m, v)
        limb = lidar.build_limb(m, v, g["hand_end"], g["wrist_tick_mm"], g["dorsal_view"])
        zs = limb.z[(limb.z > -25) & (limb.z < 25)]
        widths = np.array([limb.width_depth(q)[0] for q in zs])
        limb.z = limb.z - float(zs[np.argmin(widths)])
        return {"subject": s, "lidar": {"ok": True, **score(limb, {}, arms)}}
    except Exception as e:  # noqa: BLE001
        return {"subject": s, "lidar": {"ok": False, "error": str(e)[:160]}}


# ---------------------------------------------------------------- summaries

def summarize(results: list[dict], key: str) -> dict:
    rows = [r[key] for r in results if r.get(key, {}).get("ok")]
    out = {"n": len(rows), "failures": sum(1 for r in results if key in r and not r[key].get("ok"))}
    if not rows:
        return out
    for m in [f"width_{z}" for z in Z_EVAL] + [f"depth_{z}" for z in Z_EVAL] + [f"circ_{z}" for z in Z_EVAL] + ["sound_length", "sound_breadth"]:
        vals = np.array([r[m] for r in rows if m in r])
        if len(vals):
            out[m] = {"mae": float(np.abs(vals).mean()), "bias": float(vals.mean()), "p95": float(np.percentile(np.abs(vals), 95)), "within_2mm_pct": float((np.abs(vals) <= 2).mean() * 100)}
    for m in ("surface_mae", "surface_p95", "surface_within_2mm"):
        out[m] = float(np.mean([r[m] for r in rows]))
    dims = [f"width_{z}" for z in Z_EVAL] + [f"depth_{z}" for z in Z_EVAL] + ["sound_length", "sound_breadth"]
    allv = np.concatenate([np.abs([r[m] for r in rows if m in r]) for m in dims if any(m in r for r in rows)])
    out["all_dims_mae"] = float(allv.mean())
    out["all_dims_within_2mm_pct"] = float((allv <= 2).mean() * 100)
    return out


def by_group(results, key, field):
    groups = {}
    for r in results:
        g = r["subject"][field]
        groups.setdefault(str(g), []).append(r)
    return {g: summarize(rs, key) for g, rs in sorted(groups.items())}


def sizing_end_to_end(photo_results: list[dict]) -> dict:
    """Device length/breadth error across ANSUR II, uniform vs separate scales, with Fitted's measured error."""
    from app.hand import PALM_BASE
    from app.sizing import KNUCKLE_ALLOWANCE, RATIO_LIMITS, ansur

    B0, L0 = PALM_BASE["knuckle_breadth"], PALM_BASE["hand_length"]
    el = np.array([r["full"]["sound_length"] for r in photo_results if r["full"].get("ok") and "sound_length" in r["full"]])
    eb = np.array([r["full"]["sound_breadth"] for r in photo_results if r["full"].get("ok") and "sound_breadth" in r["full"]])
    rng = np.random.default_rng(7)
    out = {}
    for sex in ("female", "male"):
        d = ansur()[sex]
        L = np.asarray(d["handlength"], float)
        B = np.asarray(d["handbreadth"], float)
        n = len(L)
        # uniform: e-NABLE convention, perfect measurements
        s_u = (B + KNUCKLE_ALLOWANCE) / B0
        u_len = s_u * L0 - L
        # ours with measurement error resampled from the photo benchmark
        Lm = L + rng.choice(el, n) if len(el) else L
        Bm = B + rng.choice(eb, n) if len(eb) else B
        s_w = (Bm + KNUCKLE_ALLOWANCE) / B0
        s_l = Lm / L0
        r = np.clip(s_w / s_l, *RATIO_LIMITS)
        s_l = s_w / r
        o_len = s_l * L0 - L
        o_br = s_w * B0 - (B + KNUCKLE_ALLOWANCE)
        out[sex] = {
            "n": n,
            "uniform_length_mae": float(np.abs(u_len).mean()),
            "uniform_length_within_3mm_pct": float((np.abs(u_len) <= 3).mean() * 100),
            "fitted_length_mae": float(np.abs(o_len).mean()),
            "fitted_length_within_3mm_pct": float((np.abs(o_len) <= 3).mean() * 100),
            "fitted_breadth_mae": float(np.abs(o_br).mean()),
            "fitted_breadth_within_3mm_pct": float((np.abs(o_br) <= 3).mean() * 100),
        }
    return out


# ---------------------------------------------------------------- Claude photo check

def claude_check(subjects: list[dict], tmp: str) -> dict:
    """Run good and deliberately bad photo sets through the real pipeline with Claude enabled."""
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

    from app import pipeline
    from app.synthetic import render_photo

    pipeline.DATA_DIR = Path(tmp)

    def jpeg(im):
        b = io.BytesIO()
        im.save(b, "JPEG", quality=88)
        return b.getvalue()

    cases = []
    for s in subjects:
        arms = build_arms(s)
        ph = render_set(s, arms)
        top = Image.open(io.BytesIO(ph["top"]))
        cases.append(("good", s, ph))
        cases.append(("blurred", s, {**ph, "top": jpeg(top.filter(ImageFilter.GaussianBlur(9)))}))
        cases.append(("dark", s, {**ph, "top": jpeg(ImageEnhance.Brightness(top).enhance(0.18))}))
        cases.append(("swapped_views", s, {**ph, "top": ph["side"], "side": ph["top"]}))
        far = render_photo(arms["affected"], "top", wrist_xy=(95, 235), seed=s["seed"] + 5, f35=s["f35"], height=s["height"], skin=SKIN[s["skin"]])
        cases.append(("forearm_off_sheet", s, {**ph, "top": far}))
        cov = top.copy()
        dr = ImageDraw.Draw(cov)
        w, h = cov.size
        dr.rectangle([0, 0, w, h * 0.42], fill=(150, 112, 82))  # hand/sleeve covering the top markers
        dr.rectangle([0, h * 0.42, w * 0.5, h], fill=(150, 112, 82))
        cases.append(("markers_covered", s, {**ph, "top": jpeg(cov)}))

    def run(case):
        kind, s, photos = case
        job = pipeline.Job("photo")
        inp = pipeline.Inputs(mode="photo", photos=photos, recipient={"age": s["age"], "sex": s["sex"], "side": "left"})
        caught, how = False, ""
        try:
            pipeline.photo_measure(job, inp)
            chk = next((st for st in job.steps if st["key"] == "check"), {})
            how = chk.get("ai", "")[:200]
        except pipeline.InputError as e:
            caught, how = True, f"{e} | retake: {e.retake}"[:240]
        except Exception as e:  # noqa: BLE001
            caught, how = True, f"error: {e}"[:200]
        return {"kind": kind, "subject": s["id"], "caught": caught, "detail": how}

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(run, cases))
    kinds = sorted({r["kind"] for r in rows})
    summary = {}
    for k in kinds:
        rs = [r for r in rows if r["kind"] == k]
        summary[k] = {"n": len(rs), "flagged": sum(r["caught"] for r in rs)}
    bad = [r for r in rows if r["kind"] != "good"]
    good = [r for r in rows if r["kind"] == "good"]
    return {
        "bad_caught_pct": 100 * sum(r["caught"] for r in bad) / max(len(bad), 1),
        "good_false_alarm_pct": 100 * sum(r["caught"] for r in good) / max(len(good), 1),
        "by_kind": summary,
        "rows": rows,
    }


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--lidar-n", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--claude", action="store_true", help="also run the bad-photo check with Claude (paid API calls)")
    ap.add_argument("--claude-n", type=int, default=4)
    args = ap.parse_args()

    out_dir = BACKEND / "benchmark"
    out_dir.mkdir(exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fitted-bench-")
    rng = np.random.default_rng(2026)
    subjects = [make_subject(i, row, rng) for i, row in enumerate(_ansur_rows(rng, args.n))]

    t0 = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_worker_init, initargs=(tmp,)) as ex:
        photo = list(ex.map(run_photo_subject, subjects))
        print(f"photos: {len(photo)} subjects in {time.time() - t0:.0f}s", flush=True)
        t1 = time.time()
        lidar = list(ex.map(run_lidar_subject, subjects[: args.lidar_n]))
        print(f"lidar: {len(lidar)} scans in {time.time() - t1:.0f}s", flush=True)

    results = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "note": "Synthetic subjects with ANSUR II proportions; renders are cleaner than real photos, so treat these as best-case.",
        "n_subjects": len(subjects),
        "photo": {v: summarize(photo, v) for v in ("full", "no_side", "no_perspective", "tape")},
        "photo_by_sex": by_group(photo, "full", "sex"),
        "photo_by_child": by_group(photo, "full", "child"),
        "photo_by_skin": by_group(photo, "full", "skin"),
        "photo_by_lens": by_group(photo, "full", "f35"),
        "lidar": summarize(lidar, "lidar"),
        "sizing": sizing_end_to_end(photo),
        "references": {
            "HandAnthro (2026 preprint, real people, single photo)": {"overall_mae_mm": 3.80, "palm_wrist_mae_mm": 6.17},
            "e-NABLE AI HandSizer (designer's tests)": {"mae_mm": 12.0},
            "ISO 20685-1 (scan vs. trained measurer)": {"max_mean_diff_mm": 1.0},
            "e-NABLE fit window": {"mm": "2-3"},
        },
    }
    if args.claude:
        t2 = time.time()
        os.environ.setdefault("ANTHROPIC_API_KEY", _env_key())
        results["claude_photo_check"] = claude_check(subjects[: args.claude_n], tmp)
        print(f"claude check in {time.time() - t2:.0f}s", flush=True)

    (out_dir / "results.json").write_text(json.dumps(results, indent=1, default=float))
    with open(out_dir / "subjects.csv", "w", newline="") as f:
        w = csv.writer(f)
        cols = ["id", "sex", "child", "age", "k", "skin", "height", "f35"]
        metrics = ["width_0", "depth_0", "circ_0", "circ_40", "circ_80", "sound_length", "sound_breadth", "surface_mae", "surface_p95"]
        w.writerow(cols + [f"full_{m}" for m in metrics] + ["full_ok", "lidar_surface_mae"])
        lid = {r["subject"]["id"]: r["lidar"] for r in lidar}
        for r in photo:
            s = r["subject"]
            w.writerow(
                [s[c] for c in cols]
                + [round(r["full"].get(m, float("nan")), 2) if r["full"].get("ok") else "" for m in metrics]
                + [r["full"]["ok"], round(lid[s["id"]].get("surface_mae", float("nan")), 2) if s["id"] in lid and lid[s["id"]].get("ok") else ""]
            )
    print(json.dumps({k: results[k] for k in ("photo", "lidar", "sizing")}, indent=1, default=lambda x: round(float(x), 2))[:6000])


def _env_key() -> str:
    env = BACKEND.parent / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


if __name__ == "__main__":
    main()
