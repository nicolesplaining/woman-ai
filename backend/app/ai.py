"""Claude calls: photo/scan checks, device planning, design spec, and the build guide.

Every call returns structured JSON. Callers fall back to deterministic defaults when Claude is
unavailable, so the pipeline still produces printable files."""
from __future__ import annotations

import base64
import json
import os
from typing import Optional

import anthropic

from .design import FILAMENT_IDS, FILAMENTS, MOTIFS

MODEL = "claude-opus-5-5"
_client: Optional[anthropic.Anthropic] = None


class AIError(Exception):
    pass


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    return _client


def _img(data: bytes, media_type: str = "image/jpeg") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()}}


def _call(system: str, content: list, schema: dict, effort: str = "medium", max_tokens: int = 16000) -> dict:
    if not available():
        raise AIError("No Anthropic API key configured.")
    try:
        resp = client().beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIConnectionError as e:
        raise AIError(f"Couldn't reach Claude: {e}") from e
    except anthropic.RateLimitError as e:
        raise AIError("Claude is rate limited right now; try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude API error {e.status_code}: {e.message}") from e
    if resp.stop_reason == "refusal":
        raise AIError("Claude declined this request.")
    if resp.stop_reason == "max_tokens":
        raise AIError("Claude's answer was cut off.")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise AIError("Claude returned no answer.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("Claude returned malformed JSON.") from e


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR = {"type": "string"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}

SYSTEM_BASE = (
    "You are the fitting assistant inside Fitted, a tool that helps e-NABLE volunteers turn photos or a scan of a "
    "recipient's arm into a 3D-printable wrist-powered hand (the open-source Paraglider design) with a custom-fit gauntlet. "
    "e-NABLE devices are free, volunteer-made assistive devices, not medical devices; a volunteer reviews everything before "
    "printing. Be concrete and kind, and say plainly when something looks wrong."
)

# ---------------------------------------------------------------- photo check

PHOTO_SCHEMA = _obj(
    {
        "photos": {
            "type": "array",
            "items": _obj(
                {
                    "photo": STR,
                    "usable": BOOL,
                    "view_matches": BOOL,
                    "outline_ok": BOOL,
                    "hand_end": {"type": "string", "enum": ["top", "bottom", "not_visible"]},
                    "wrist_tick_mm": NUM,
                    "knuckle_tick_mm": NUM,
                    "issues": {"type": "array", "items": STR},
                    "retake_instructions": STR,
                }
            ),
        },
        "limb_difference": {
            "type": "string",
            "enum": ["none_visible", "partial_hand_fingers_absent", "partial_hand_some_fingers", "partial_hand_other", "transradial", "unclear"],
        },
        "limb_difference_notes": STR,
        "suitable_for_wrist_powered": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "summary": STR,
    }
)


def inspect_photos(photos: list[tuple[str, str, bytes]], context: dict) -> dict:
    """photos: (id, description of the expected view, overlay JPEG)."""
    content: list = []
    for pid, desc, data in photos:
        content.append({"type": "text", "text": f"Photo {pid}: expected view: {desc}"})
        content.append(_img(data))
    content.append(
        {
            "type": "text",
            "text": (
                "Each image is the photo after we straightened it using the printed marker sheet. Our automatic outline of the arm "
                "is drawn in green, and a blue ruler runs along the arm with tick labels in millimeters (0 at the end nearest the "
                "hand when the hand is at the top of the image).\n\n"
                f"Context from the app: {json.dumps(context)}\n\n"
                "For each photo:\n"
                "- usable: can we measure from it? view_matches: does it show the expected view?\n"
                "- outline_ok: does the green outline follow the skin edge (not shadows, sleeves, or the table)?\n"
                "- hand_end: is the hand (or residual hand) toward the top or bottom of the image?\n"
                "- wrist_tick_mm: the ruler value at the wrist crease (where the hand meets the forearm). -1 if not visible.\n"
                "- knuckle_tick_mm: for the sound-hand photo only, the ruler value at the knuckle line (base of the fingers); else -1.\n"
                "- issues and retake_instructions: short, specific, written for a parent or teen at home. Empty if fine.\n"
                "Also classify the visible limb difference on the affected side and whether a wrist-powered hand looks suitable "
                "(needs a working wrist and some palm). Teammates testing the app may have no limb difference; say none_visible."
            ),
        }
    )
    return _call(SYSTEM_BASE, content, PHOTO_SCHEMA, effort="medium")


SCAN_SCHEMA = _obj(
    {
        "hand_end": {"type": "string", "enum": ["top", "bottom"]},
        "wrist_tick_mm": NUM,
        "dorsal_view": {"type": "integer", "enum": [1, 2]},
        "scan_ok": BOOL,
        "issues": {"type": "array", "items": STR},
        "limb_difference": STR,
        "summary": STR,
    }
)


def inspect_scan(images: list[bytes], context: dict) -> dict:
    content: list = []
    for i, data in enumerate(images):
        content.append({"type": "text", "text": f"Scan view {i + 1}"})
        content.append(_img(data))
    content.append(
        {
            "type": "text",
            "text": (
                "These are two renders of an uploaded 3D scan of an arm, seen from opposite sides, with a millimeter ruler "
                "along the arm (0 at the top of the image).\n"
                f"Context: {json.dumps(context)}\n"
                "Tell us which end is the hand, the ruler value at the wrist crease, which view shows the back of the hand/forearm "
                "(dorsal side: knuckles, no palm creases), whether the scan looks complete enough to fit a gauntlet, and any issues."
            ),
        }
    )
    return _call(SYSTEM_BASE, content, SCAN_SCHEMA, effort="medium")


# ---------------------------------------------------------------- device plan

PLAN_SCHEMA = _obj(
    {
        "device": {"type": "string", "enum": ["paraglider_wrist_powered", "refer_to_volunteer"]},
        "suitability_rationale": STR,
        "gap_mm": NUM,
        "gauntlet_length_mm": NUM,
        "sizing_explanation": STR,
        "warnings": {"type": "array", "items": STR},
        "volunteer_checks": {"type": "array", "items": STR},
    }
)


def plan_device(measurements: dict, sizing: dict, recipient: dict, limb_check: dict, bounds: dict) -> dict:
    text = (
        "Plan the device for this recipient.\n\n"
        f"Recipient: {json.dumps(recipient)}\n"
        f"Measurements (mm): {json.dumps(measurements)}\n"
        f"Photo/scan check: {json.dumps(limb_check)}\n"
        f"Computed sizing (code, deterministic): {json.dumps(sizing, default=float)}\n"
        f"Allowed ranges: {json.dumps(bounds)}\n\n"
        "Rules: a wrist-powered hand needs a working wrist and enough palm to push against the device; otherwise choose "
        "refer_to_volunteer and explain. The palm and finger scales are fixed by the code above; don't change them, but explain "
        "them in plain words (width and length are scaled separately so the device matches this person's proportions). Pick "
        "the padding gap and gauntlet length within the allowed ranges: more gap for photo input or growing children, less for "
        "LiDAR scans of adults. List concrete warnings and the checks a volunteer should do before printing."
    )
    return _call(SYSTEM_BASE, [{"type": "text", "text": text}], PLAN_SCHEMA, effort="low")


# ---------------------------------------------------------------- design

DESIGN_SCHEMA = _obj(
    {
        "palm_color": {"type": "string", "enum": FILAMENT_IDS},
        "fingers_color": {"type": "string", "enum": FILAMENT_IDS},
        "gauntlet_color": {"type": "string", "enum": FILAMENT_IDS},
        "accent_color": {"type": "string", "enum": FILAMENT_IDS},
        "motif": {"type": "string", "enum": [*MOTIFS.keys(), "custom"]},
        "custom_svg": STR,
        "size_mm": NUM,
        "spacing_mm": NUM,
        "pattern_style": {"type": "string", "enum": ["raised", "recessed"]},
        "name_text": STR,
        "summary": STR,
        "ip_note": STR,
    }
)


def design_spec(request_text: str, ref_images: list[tuple[bytes, str]], recipient: dict) -> dict:
    palette = ", ".join(f"{f['id']} ({f['name']}, {f['hex']})" for f in FILAMENTS)
    motifs = ", ".join(f"{k} ({v})" for k, v in MOTIFS.items())
    content: list = []
    for data, mt in ref_images[:4]:
        content.append(_img(data, mt))
    content.append(
        {
            "type": "text",
            "text": (
                "Design the look of this device from the recipient's request"
                + (" and the reference images above" if ref_images else "")
                + ".\n\n"
                f"Request: {request_text or '(none given)'}\n"
                f"Recipient: {json.dumps({k: recipient.get(k) for k in ('name', 'age')})}\n\n"
                f"Filament colors available (use these ids): {palette}\n"
                f"Pattern motifs (wrapped around the gauntlet as a raised or recessed texture in the gauntlet's own color, since most volunteers print one color per part): {motifs}\n"
                "accent_color is used for the finger joints (phalanges) and the tensioner box.\n"
                "Use 'custom' only if no library motif fits; then custom_svg must be a small, simple SVG (viewBox 0 0 100 100, "
                "only filled path/circle/rect/polygon shapes, one bold silhouette, no text, no strokes thinner than 8 units). "
                "Otherwise custom_svg is an empty string.\n"
                "size_mm 8-24 and spacing_mm 4-16 control the motif. name_text is optional text on the gauntlet (max 14 characters): "
                "only include the recipient's name if they asked for it.\n"
                "For characters, brands or logos (e.g. a superhero), don't copy logos or trademarked marks: translate the request "
                "into an inspired-by palette and generic motifs, and explain that in ip_note (empty if not relevant). Reference "
                "images are only for colors and motifs; don't describe any person in them.\n"
                "summary: one or two friendly sentences describing the look, written to the recipient."
            ),
        }
    )
    return _call(SYSTEM_BASE, content, DESIGN_SCHEMA, effort="low")


# ---------------------------------------------------------------- build guide

PACKET_SCHEMA = _obj({"volunteer_guide_markdown": STR, "recipient_summary": STR, "recipient_language": STR})


def build_packet(job_summary: dict, language: str) -> dict:
    text = (
        "Write the build packet for this device.\n\n"
        f"{json.dumps(job_summary, default=float)}\n\n"
        "volunteer_guide_markdown: a concise, practical Markdown guide for the e-NABLE volunteer with these sections: "
        "## Summary, ## Print first: fit-test ring, ## Print list (table: part, quantity, color, est. time), "
        "## Print settings, ## Hardware, ## Assembly notes (short; point to the Paraglider/Phoenix Reborn instructions for "
        "the hand itself and explain what is different about the custom gauntlet: it is already formed, so no heat-forming), "
        "## Fit checks before delivery, ## Warnings. Use the numbers given; don't invent measurements.\n"
        f"recipient_summary: 3-5 warm, plain sentences for the recipient and their family in {language}, explaining what "
        "was made and what happens next (a volunteer checks the fit with a small test ring first). recipient_language: the "
        "language used."
    )
    return _call(SYSTEM_BASE, [{"type": "text", "text": text}], PACKET_SCHEMA, effort="low", max_tokens=12000)
