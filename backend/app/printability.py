"""Printability checks, rough print estimates, build-plate packing, and colored 3MF export."""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import numpy as np
import trimesh

PLA_DENSITY = 1.24  # g/cm^3
FLOW_MM3_S = 7.0  # conservative average volumetric speed for a 0.4 mm nozzle
SPACING = 6.0


@dataclass
class Part:
    id: str
    label: str
    mesh: trimesh.Trimesh  # already in print orientation, sitting on z = 0
    count: int
    color: str  # hex
    color_name: str


def orient_gauntlet(m: trimesh.Trimesh) -> trimesh.Trimesh:
    """Stand the gauntlet on its elbow end (arm z -> -Z): tube walls print vertically, no supports."""
    out = m.copy()
    out.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]))
    out.apply_translation(-out.bounds[0])
    return out


def on_bed(m: trimesh.Trimesh) -> trimesh.Trimesh:
    out = m.copy()
    out.apply_translation([-out.bounds[0][0], -out.bounds[0][1], -out.bounds[0][2]])
    return out


def check(part: Part, bed: tuple[float, float, float]) -> dict:
    m = part.mesh.copy()
    m.merge_vertices()
    try:
        from .limb import to_manifold

        watertight = to_manifold(m.vertices, m.faces).status().name == "NoError"
    except Exception:
        watertight = bool(m.is_watertight)
    ext = m.extents
    fits = bool(ext[0] <= bed[0] and ext[1] <= bed[1] and ext[2] <= bed[2]) or bool(ext[1] <= bed[0] and ext[0] <= bed[1] and ext[2] <= bed[2])
    n = m.face_normals
    a = m.area_faces
    zc = m.triangles_center[:, 2]
    down = (n[:, 2] < -np.cos(np.radians(45))) & (zc > 0.6)
    overhang = float(a[down].sum() / max(a.sum(), 1e-9))
    vol = float(abs(m.volume)) / 1000.0  # cm^3
    shell = min(1.0, (m.area * 1.2 / 1000.0) / max(vol, 1e-6))
    fill = shell + 0.3 * (1 - shell)
    grams = vol * fill * PLA_DENSITY
    minutes = (vol * fill * 1000 / FLOW_MM3_S) / 60 * 1.35 + 2
    status = "ok"
    notes = []
    if not watertight:
        status = "warn"
        notes.append("Mesh has small gaps; most slicers repair this automatically.")
    if not fits:
        status = "fail"
        notes.append(f"Doesn't fit a {bed[0]:.0f}x{bed[1]:.0f}x{bed[2]:.0f} mm bed.")
    if overhang > 0.08 and part.id in ("gauntlet", "fit_ring"):
        status = "warn" if status == "ok" else status
        notes.append(f"{overhang * 100:.0f}% of the surface overhangs more than 45 degrees; check supports in the slicer.")
    if part.id not in ("gauntlet", "fit_ring"):
        notes.append("Stock Paraglider part: print flat as designed, no supports.")
    return {
        "id": part.id,
        "label": part.label,
        "count": part.count,
        "color": part.color,
        "color_name": part.color_name,
        "size_mm": [round(float(x), 1) for x in ext],
        "watertight": watertight,
        "fits_bed": fits,
        "overhang_pct": round(overhang * 100, 1),
        "grams_each": round(grams, 1),
        "minutes_each": round(minutes),
        "status": status,
        "notes": notes,
    }


def pack(parts: list[Part], bed: tuple[float, float, float]) -> list[list[tuple[Part, np.ndarray]]]:
    """Shelf-pack every copy of every part onto as many plates as needed. Returns [(part, translation)] per plate."""
    items = []
    for p in parts:
        for _ in range(p.count):
            items.append(p)
    items.sort(key=lambda p: -p.mesh.extents[1])
    plates: list[list] = []
    cur, x, y, shelf_h = [], 0.0, 0.0, 0.0
    for p in items:
        w, h = p.mesh.extents[0], p.mesh.extents[1]
        if w > bed[0] or h > bed[1]:
            plates.append([(p, np.array([0.0, 0.0, 0.0]))])
            continue
        if x + w > bed[0]:
            x, y, shelf_h = 0.0, y + shelf_h + SPACING, 0.0
        if y + h > bed[1]:
            plates.append(cur)
            cur, x, y, shelf_h = [], 0.0, 0.0, 0.0
        cur.append((p, np.array([x, y, 0.0])))
        x += w + SPACING
        shelf_h = max(shelf_h, h)
    if cur:
        plates.append(cur)
    return plates


def write_3mf(plate: list[tuple[Part, np.ndarray]]) -> bytes:
    """Core-spec 3MF with one object per placed copy and per-object base material colors."""
    colors = []
    for p, _ in plate:
        if p.color not in colors:
            colors.append(p.color)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
        "<resources>",
        '<basematerials id="1">',
    ]
    for c in colors:
        lines.append(f'<base name="{c}" displaycolor="{c}FF" />')
    lines.append("</basematerials>")
    build = []
    for i, (p, t) in enumerate(plate, start=2):
        m = p.mesh
        lines.append(f'<object id="{i}" name="{p.label}" type="model" pid="1" pindex="{colors.index(p.color)}"><mesh><vertices>')
        v = m.vertices + t
        lines.extend(f'<vertex x="{a:.3f}" y="{b:.3f}" z="{c:.3f}" />' for a, b, c in v)
        lines.append("</vertices><triangles>")
        lines.extend(f'<triangle v1="{a}" v2="{b}" v3="{c}" />' for a, b, c in m.faces)
        lines.append("</triangles></mesh></object>")
        build.append(f'<item objectid="{i}" />')
    lines.append("</resources><build>")
    lines.extend(build)
    lines.append("</build></model>")
    model = "\n".join(lines)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        )
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()
