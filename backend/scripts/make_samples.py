"""Generate the synthetic sample set used by the "Try a sample" buttons and accuracy tests.

Writes samples/partial_hand/{A_top.jpg, B_side.jpg, C_sound.jpg, scan.ply, truth.json}.
Run: .venv/bin/python scripts/make_samples.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.synthetic import RESIDUAL, SOUND, arm_model, lidar_scan, render_photo, truth  # noqa: E402

out = Path(__file__).resolve().parent.parent / "samples" / "partial_hand"
out.mkdir(parents=True, exist_ok=True)
K = 0.8  # about a 10-year-old
affected = arm_model(RESIDUAL, k=K)
sound = arm_model(SOUND, k=K)
(out / "A_top.jpg").write_bytes(render_photo(affected, "top", wrist_xy=(95.0, 70.0), seed=1, height=750))
(out / "B_side.jpg").write_bytes(render_photo(affected, "side", wrist_xy=(98.0, 68.0), seed=2, height=750))
(out / "C_sound.jpg").write_bytes(render_photo(sound, "top", wrist_xy=(92.0, 185.0), seed=3, height=750))
lidar_scan(affected, seed=4).export(out / "scan.ply")
t = truth(affected, RESIDUAL)
t["sound_hand"] = {"length": 180.0 * K, "breadth": 78.0 * K}
t["residual_palm_width"] = 72.0 * K
t["note"] = "Synthetic child (~10 years) with a partial hand (palm present, fingers absent): ANSUR II female median proportions scaled by 0.8."
(out / "truth.json").write_text(json.dumps(t, indent=1))
print("wrote", sorted(p.name for p in out.iterdir()))
