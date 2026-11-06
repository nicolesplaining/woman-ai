"""Download ANSUR II (public, US Army 2012 anthropometric survey) and keep the hand/arm columns we use.

Writes app/data/ansur_hands.json. Run: .venv/bin/python scripts/build_ansur.py
"""
import csv
import io
import json
import urllib.request
from pathlib import Path

URLS = {
    "female": "https://tools.openlab.psu.edu/publicData/ANSUR_II_FEMALE_Public.csv",
    "male": "https://tools.openlab.psu.edu/publicData/ANSUR_II_MALE_Public.csv",
}
COLS = ["handlength", "handbreadth", "wristcircumference", "forearmcircumferenceflexed", "palmlength"]

out = {"source": "ANSUR II public data, Penn State OPEN Design Lab (https://www.openlab.psu.edu/ansur2/)", "units": "mm"}
for sex, url in URLS.items():
    text = urllib.request.urlopen(url, timeout=60).read().decode("latin-1")
    rows = list(csv.DictReader(io.StringIO(text)))
    out[sex] = {c: [int(float(r[c])) for r in rows] for c in COLS}
    print(sex, len(rows))
path = Path(__file__).resolve().parent.parent / "app" / "data" / "ansur_hands.json"
path.write_text(json.dumps(out, separators=(",", ":")))
print("wrote", path, path.stat().st_size // 1024, "KB")
