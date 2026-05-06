"""Parse vanilla `<Land>.c` and spawn Blender Empties + collision meshes for one room.

Placement arrays (RoomObject / RoomActor / Collectable / SpriteActor) are parsed
and spawned as Empties. The original entry text is stashed verbatim as
`obj["ct_raw_entry"]` so the round-trip exporter can write it back unchanged unless
the Empty has moved.

Collision meshes: for each unique model id in the room's RoomObject array,
resolve id -> stageModels[id] -> ModelCollision symbol -> asset .inc.c files, then
build a Blender Mesh tagged with is_collision=True.
"""

from __future__ import annotations
import re
from pathlib import Path


ROOM_ARRAY_SPECS = [
    ("RoomObject",  "objects",      0),  # pos is first field
    ("RoomActor",   "actors",       1),  # after `id`
    ("Collectable", "collectables", 1),  # after `id`
    ("SpriteActor", "sprites",      2),  # after `size`, `spriteIndex`
]


def _strip_line_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def find_array_body(source: str, struct: str, symbol: str) -> str | None:
    """Return the `{ ... }` body of `<struct> <symbol>[] = { ... };` or None."""
    pattern = re.compile(
        rf"\b{re.escape(struct)}\s+{re.escape(symbol)}\s*\[\s*\]\s*=\s*\{{",
        re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        return None
    # Walk braces from the opening { to find the matching close.
    depth = 1
    i = m.end()
    while i < len(source) and depth > 0:
        c = source[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return source[m.end():i]
        i += 1
    return None


def split_entries(body: str) -> list[str]:
    """Split a `{ ... }, { ... }, ...` body into the inner text of each top-level
    brace block. Line comments are stripped first so `// foo` doesn't confuse us.
    """
    body = _strip_line_comments(body)
    entries = []
    depth = 0
    start = -1
    for i, c in enumerate(body):
        if c == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                entries.append(body[start:i])
                start = -1
    return entries


def split_top_level_fields(entry: str) -> list[str]:
    """Split an entry's body on top-level commas, treating `{...}` as one token."""
    fields = []
    depth = 0
    buf = []
    for c in entry:
        if c == "{":
            depth += 1
            buf.append(c)
        elif c == "}":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            fields.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    tail = "".join(buf).strip()
    if tail:
        fields.append(tail)
    return fields


def parse_vec3(text: str) -> tuple[float, float, float] | None:
    """Parse `{ x, y, z }` (with optional `f` suffixes) -> tuple, or None."""
    inner = text.strip()
    if not (inner.startswith("{") and inner.endswith("}")):
        return None
    parts = inner[1:-1].split(",")
    if len(parts) != 3:
        return None
    try:
        return tuple(float(p.strip().rstrip("fF")) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def ct_position_to_blender(pos: tuple[float, float, float]) -> tuple[float, float, float]:
    """Inverse of manifest_export._vec3 : CT (X, Y, Z, Y-up) -> Blender (X, -Z, Y, Z-up)."""
    x, y, z = pos
    return (x, -z, y)


def ct_scale_to_blender(scale: tuple[float, float, float]) -> tuple[float, float, float]:
    """CT scale (sx, sy, sz, Y-up) -> Blender (sx, sz, sy, Z-up).  No axis negation."""
    sx, sy, sz = scale
    return (sx, sz, sy)


# Field indices inside a flat RoomObject entry (same order as split_top_level_fields).
ROOM_OBJECT_SCALE_FIELD = 1   # Vec3f scale
ROOM_OBJECT_MODEL_FIELD = 16  # s32 id 


def list_room_variants(land_c_path: Path, land: str) -> list[str]:
    """Scan the source file for all room-set prefixes (e.g. '' for interior,
    'ext_' for JungleLand exterior). Returns sorted list of prefix strings."""
    source = land_c_path.read_text()
    # Match: <Land>_<variant>room<N>_objects
    pat = re.compile(rf"\b{re.escape(land)}_([a-z_]*)room\d+_objects\b")
    prefixes: set[str] = set()
    for m in pat.finditer(source):
        prefixes.add(m.group(1))  # e.g. "" or "ext_"
    return sorted(prefixes)


def parse_room_arrays(
    land_c_path: Path, land: str, room_id: int, room_variant: str = ""
) -> dict[str, list[dict]]:
    """Return {array_kind: [ {raw, position?}, ... ]} for the four per-room arrays.

    room_variant is an optional prefix inserted between the land name and "room",
    e.g. "ext_" gives `JungleLand_ext_room1_objects`.

    Missing arrays return [] for that key. Entries where position can't be
    parsed still appear in the list with `position=None` (e.g. Null sentinels).
    """
    source = land_c_path.read_text()
    out: dict[str, list[dict]] = {}
    for struct, suffix, pos_index in ROOM_ARRAY_SPECS:
        symbol = f"{land}_{room_variant}room{room_id}_{suffix}"
        body = find_array_body(source, struct, symbol)
        if body is None:
            out[suffix] = []
            continue
        entries: list[dict] = []
        for raw in split_entries(body):
            fields = split_top_level_fields(raw)
            position = None
            if pos_index < len(fields):
                position = parse_vec3(fields[pos_index])
            entries.append({
                "raw": raw.strip(),
                "fields": fields,
                "position": position,
            })
        out[suffix] = entries
    return out



# Collision asset resolution
# Field index of `id` (model index) inside a flat RoomObject entry.
ROOM_OBJECT_MODEL_FIELD = 16

def parse_model_enum(source: str) -> dict[str, int]:
    """Parse all C enum entries from source -> {name: int}."""
    result: dict[str, int] = {}
    # Find every `enum <optional_name> { ... }` block.
    for body_m in re.finditer(r"\benum\b[^{]*\{([^}]*)\}", source, re.DOTALL):
        body = body_m.group(1)
        val = 0
        for line in body.splitlines():
            line = re.sub(r"//.*", "", line).strip().rstrip(",")
            if not line or line.startswith("#") or line.startswith("/*"):
                continue
            if "=" in line:
                name, rhs = line.split("=", 1)
                name = name.strip()
                try:
                    val = int(rhs.strip().rstrip(","))
                except ValueError:
                    val += 1
                    name = name  # keep name, skip bad RHS
            else:
                name = line.strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                result[name] = val
                val += 1
    return result


def parse_stage_models(source: str, land: str) -> list[str]:
    """Return a list of collision symbols indexed by model id, parsed from
    `StageModel <Land>_stageModels[]`. Each slot holds the collision symbol
    (e.g. `Global_ALPole_collision`) or empty string if unparseable."""
    body = find_array_body(source, "StageModel", f"{land}_stageModels")
    if body is None:
        return []
    entries = split_entries(body)
    result: list[str] = []
    for raw in entries:
        # Each entry: { Gfx*, &<Symbol>_collision, {pad} }
        m = re.search(r"&\s*([A-Za-z0-9_]+_collision)\b", raw)
        result.append(m.group(1) if m else "")
    return result


def resolve_model_id(field_text: str, enum_map: dict[str, int]) -> int | None:
    """Resolve a RoomObject id field : either a literal int or an enum name."""
    t = field_text.strip()
    if re.match(r"^-?\d+$", t):
        return int(t)
    return enum_map.get(t)


def find_collision_asset_files(repo_root: "Path", collision_sym: str) -> tuple["Path", "Path"] | None:
    """Given `Global_ALPole_collision`, find:
        assets/levelGroup/Global/ALPole/ALPole.colVerts.inc.c
        assets/levelGroup/Global/ALPole/ALPole.colTris.inc.c
    Returns (verts_path, tris_path) or None if not found.
    """
    from pathlib import Path
    base = collision_sym[:-len("_collision")] if collision_sym.endswith("_collision") else collision_sym   # e.g. Global_ALPole
    # Split on first underscore to get land prefix and model short name.
    if "_" not in base:
        return None
    land_prefix, short_name = base.split("_", 1)
    asset_dir = Path(repo_root) / "assets" / "levelGroup" / land_prefix / short_name
    verts = asset_dir / f"{short_name}.colVerts.inc.c"
    tris  = asset_dir / f"{short_name}.colTris.inc.c"
    if verts.exists() and tris.exists():
        return (verts, tris)
    return None


def parse_col_verts(text: str) -> list[tuple[float, float, float]]:
    """Parse `{x, y, z},` lines from a .colVerts.inc.c body."""
    verts = []
    for m in re.finditer(r"\{([^}]+)\}", text):
        parts = m.group(1).split(",")
        if len(parts) == 3:
            try:
                verts.append(tuple(float(p.strip().rstrip("fF")) for p in parts))  # type: ignore[misc]
            except ValueError:
                pass
    return verts


def parse_col_tris(text: str) -> list[tuple[int, int, int]]:
    """Parse `{a, b, c},` lines from a .colTris.inc.c body."""
    tris = []
    for m in re.finditer(r"\{([^}]+)\}", text):
        parts = m.group(1).split(",")
        if len(parts) == 3:
            try:
                tris.append(tuple(int(p.strip()) for p in parts))  # type: ignore[misc]
            except ValueError:
                pass
    return tris


def load_collision_meshes(
    source: str,
    land: str,
    repo_root: "Path",
    object_entries: list[dict],
    enum_map: dict[str, int] | None = None,
) -> list[dict]:
    """For each unique model id in object_entries, resolve to a collision symbol,
    find the asset files, and return a list of mesh dicts:
        { symbol, name, verts: [(x,y,z)...], tris: [(a,b,c)...] }
    verts are already converted to Blender coordinates.
    """
    if enum_map is None:
        enum_map = parse_model_enum(source)
    stage_models = parse_stage_models(source, land)

    seen: set[str] = set()
    meshes: list[dict] = []

    for entry in object_entries:
        fields = entry.get("fields", [])
        if len(fields) <= ROOM_OBJECT_MODEL_FIELD:
            continue
        model_id = resolve_model_id(fields[ROOM_OBJECT_MODEL_FIELD], enum_map)
        if model_id is None or model_id <= 0 or model_id >= len(stage_models):
            continue
        sym = stage_models[model_id]
        if not sym or sym in seen:
            continue
        seen.add(sym)

        paths = find_collision_asset_files(repo_root, sym)
        if paths is None:
            continue
        verts_path, tris_path = paths
        raw_verts = parse_col_verts(verts_path.read_text())
        raw_tris  = parse_col_tris(tris_path.read_text())
        # Convert CT (X, Y, Z, Y-up) -> Blender (X, -Z, Y, Z-up)
        bl_verts = [ct_position_to_blender(v) for v in raw_verts]
        meshes.append({
            "symbol": sym,
            "name": sym[:-len("_collision")] if sym.endswith("_collision") else sym,
            "verts": bl_verts,
            "tris": raw_tris,
        })

    return meshes


def iter_object_model_placements(
    source: str,
    land: str,
    object_entries: list[dict],
    enum_map: dict[str, int] | None = None,
) -> list[dict]:
    """Return [{symbol, position}] for each RoomObject entry with a resolvable model.
    position is a CT-space (x,y,z) tuple. Entries without a valid model or position
    are skipped (e.g. null sentinels).
    """
    if enum_map is None:
        enum_map = parse_model_enum(source)
    stage_models = parse_stage_models(source, land)

    result: list[dict] = []
    for entry in object_entries:
        fields = entry.get("fields", [])
        if len(fields) <= ROOM_OBJECT_MODEL_FIELD:
            continue
        model_id = resolve_model_id(fields[ROOM_OBJECT_MODEL_FIELD], enum_map)
        if model_id is None or model_id <= 0 or model_id >= len(stage_models):
            continue
        sym = stage_models[model_id]
        if not sym:
            continue
        pos = entry.get("position")
        if pos is None:
            continue
        pl: dict = {"symbol": sym, "position": pos}
        if len(fields) > ROOM_OBJECT_SCALE_FIELD:
            scale = parse_vec3(fields[ROOM_OBJECT_SCALE_FIELD])
            if scale is not None:
                pl["scale"] = scale
        result.append(pl)
    return result
