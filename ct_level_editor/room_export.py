"""Round-trip exporter: re-emit vanilla room arrays from Blender scene Empties.

Each Empty imported by CT_OT_import_room carries:
  obj["ct_raw_entry"]  : verbatim original C struct body text
  obj["ct_array_kind"] : "objects" / "actors" / "collectables" / "sprites"
  obj["ct_room_id"]    : int room index
  obj["ct_land"]       : land name string
"""

from __future__ import annotations
import re
from pathlib import Path

_IS_VANILLA_ID = re.compile(r"^\d+$")  # raw integer index


def _model_name_to_define(name: str) -> str:
    """Convert a stageModels asset name to its codegen #define identifier.
    e.g. "JungleLand_intZero_noHole" -> "JUNGLELAND_INTZERO_NOHOLE_MODEL"
    """
    return name.upper() + "_MODEL"

import bpy

from .room_import import split_top_level_fields as _split_fields

# Blender -> CT axis swap (inverse of room_import.ct_position_to_blender).
def _blender_to_ct(loc) -> tuple[float, float, float]:
    x, y, z = loc
    return (x, z, -y)


def _blender_scale_to_ct(scale) -> tuple[float, float, float]:
    """Inverse of room_import.ct_scale_to_blender."""
    bx, by, bz = scale
    return (bx, bz, by)


def _replace_field(raw: str, n: int, new_text: str) -> str:
    """Replace the n-th top-level field in raw (split-and-rejoin; preserves commas)."""
    fields = _split_fields(raw)
    if n >= len(fields):
        return raw
    fields[n] = new_text
    return ", ".join(fields)


def _format_vec3(v: tuple[float, float, float]) -> str:
    return "{" + ", ".join(f"{c:.4f}" for c in v) + "}"


_POS_FIELD_INDEX = {
    "objects":      0,  # pos is the first {x,y,z} block
    "actors":       0,  # id is a scalar; pos is still the first {x,y,z} block
    "collectables": 0,  # same : id scalar, then pos vec3 block 0
    "sprites":      0,  # sprite_type and sprite_id are scalars; pos is block 0
}

# Struct type names used in the array declarations.
_STRUCT_TYPE = {
    "objects":      "RoomObject",
    "actors":       "RoomActor",
    "collectables": "Collectable",
    "sprites":      "SpriteActor",
}


def _replace_nth_vec3(raw: str, n: int, new_vec: tuple[float, float, float]) -> str:
    """Replace the n-th top-level `{x,y,z}` brace block in `raw` with new_vec.
    Counts only blocks that look like vec3 (three comma-separated numbers).
    Returns raw unchanged if the n-th block isn't found.
    """
    count = 0
    i = 0
    while i < len(raw):
        if raw[i] != "{":
            i += 1
            continue
        # Find matching close brace.
        depth = 1
        j = i + 1
        while j < len(raw) and depth > 0:
            if raw[j] == "{":
                depth += 1
            elif raw[j] == "}":
                depth -= 1
            j += 1
        block = raw[i:j]
        inner = block[1:-1]
        parts = [p.strip() for p in inner.split(",")]
        # Only count blocks that are exactly three numeric tokens.
        if len(parts) == 3:
            def _is_num(s: str) -> bool:
                return bool(re.match(r"^-?\d+(\.\d+)?(f|F)?$", s.strip()))
            if all(_is_num(p) for p in parts):
                if count == n:
                    return raw[:i] + _format_vec3(new_vec) + raw[j:]
                count += 1
        i = j
    return raw


_MOVE_THRESHOLD = 0.001  # units; below this we treat the object as unmoved
_SCALE_THRESHOLD = 0.0001

# Templates for newly spawned entries that have no ct_raw_entry yet.
_ACTOR_TEMPLATE = (
    "ACTOR_NULL, {0.0, 0.0, 0.0}, "
    "0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0, "
    "0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0.0, 0.0, 0, 0"
)
_COLLECTABLE_TEMPLATE = "0, {0.0, 0.0, 0.0}, 4294967295, 0, 0, 0"

# Null-sentinel entries appended at the end of generated arrays that have no imported sentinel.
_NULL_SENTINELS = {
    "objects":      "{0.0,0.0,0.0}, {0.0,0.0,0.0}, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL, NULL, 0, 0, 0, 0, 0, 0, 0, 0, 0",
    "actors":       "0, {0.0,0.0,0.0}, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0",
    "collectables": "0, {0.0,0.0,0.0}, 4294967295, 0, 0, 0",
    "sprites":      "-1, 0, {-1.0,-1.0,-1.0}, {-1.0,32.0,32.0}, 1, 0, 0.0, 0, 0, 0, 0, 0, {-1, -1, 0, 0}",
}

# Minimal dispatch/defaults for new CT_KINDS RoomObjects (no ct_raw_entry).
_KIND_DISPATCH: dict[str, int] = {
    "static_mesh": 0x00, "moving_platform": 0x05, "tilt_platform": 0x06,
    "spin_door": 0x07, "pole_grabbable": 0x08, "moving_object_simple": 0x09,
    "fixed_cam_trigger": 0x12, "exit_trigger": 0x17, "door": 0x19,
}
_RO_DEFAULTS = (
    "0, 0.0, 7, 0, 1400.0, 1000.0, -1000.0, 0.0, 0, 90, 0, 1000, 0, 0"
)


def _entry_for_new_object(obj: bpy.types.Object) -> str:
    """Generate a RoomObject C entry for a newly-spawned CT_KINDS empty.
    Called when the object has ct.kind but no ct_raw_entry or ct_array_kind.
    """
    pos = _blender_to_ct(obj.location)
    scale = _blender_scale_to_ct(obj.scale)
    dispatch = _KIND_DISPATCH.get(getattr(getattr(obj, "ct", None), "kind", ""), 0x00)
    model = ""
    ct = getattr(obj, "ct", None)
    if ct:
        model = ct.model_enum.strip() if ct.model_enum else ct.model_name.strip()
    if not model:
        model = "0"

    # exit_trigger: pack direction into keyframes slot, target_arg into noKeyframes.
    kf_temp, no_kf = "0", "90"
    if ct and ct.kind == "exit_trigger":
        dir_map = {"N": 0, "E": 1, "S": 2, "W": 3}
        kf_temp = str(dir_map.get(ct.exit_direction, 0))
        no_kf = str(ct.exit_target_arg)

    pos_s   = f"{{{pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}}}"
    scale_s = f"{{{scale[0]:.4f}, {scale[1]:.4f}, {scale[2]:.4f}}}"
    fields  = [
        pos_s, scale_s,
        "0", "0.0", "7", "0",
        "1400.0", "1000.0", "-1000.0", "0.0",
        kf_temp, no_kf,
        "0", "1000", "0", "0",
        model,
        "-1", "-1", "-1",
        "NULL", "NULL",
        "0", "0",
        f"0x{dispatch:02X}",
        "4", "4", "0", "-1", "0", "0",
    ]
    return ", ".join(fields)


def _entry_for_obj(obj: bpy.types.Object) -> str:
    """Return the C entry text for one Empty, patching position, scale, and id if changed."""
    raw: str = obj.get("ct_raw_entry", "")
    array_kind: str = obj.get("ct_array_kind", "objects")

    # Generate template for newly spawned entries that have no imported raw entry.
    if not raw:
        if array_kind == "actors":
            raw = _ACTOR_TEMPLATE
        elif array_kind == "collectables":
            raw = _COLLECTABLE_TEMPLATE
        else:
            return ""

    # pos
    original_pos = obj.get("ct_original_pos")
    ct_pos = _blender_to_ct(obj.location)
    if original_pos is not None:
        moved = sum(abs(ct_pos[i] - original_pos[i]) for i in range(3)) > _MOVE_THRESHOLD
    else:
        moved = True
    if moved:
        raw = _replace_nth_vec3(raw, _POS_FIELD_INDEX.get(array_kind, 0), ct_pos)

    # scale
    if array_kind == "objects":
        original_scale = obj.get("ct_original_scale")
        ct_scale = _blender_scale_to_ct(obj.scale)
        if original_scale is not None:
            rescaled = sum(abs(ct_scale[i] - original_scale[i]) for i in range(3)) > _SCALE_THRESHOLD
        else:
            rescaled = True
        if rescaled:
            raw = _replace_nth_vec3(raw, 1, ct_scale)  # vec3 block index 1 = scale field

    # other
    raw = _maybe_patch_id(obj, array_kind, raw)

    return raw


def _maybe_patch_id(obj: bpy.types.Object, array_kind: str, raw: str) -> str:
    """If the user changed the model/actor/sprite enum, splice it into the raw entry."""
    ct = getattr(obj, "ct", None)
    if ct is None:
        return raw
    if array_kind == "objects":
        override = getattr(ct, "model_id_override", "").strip()
        if override and not override.startswith("_"):
            # New asset name -> derive the #define codegen will emit for it.
            if not _IS_VANILLA_ID.match(override) and not override.endswith("_MODEL"):
                new_id = _model_name_to_define(override)
            else:
                new_id = override  # already a define (FOO_MODEL) or raw integer
        else:
            new_id = ct.model_enum.strip() if ct.model_enum else ""
        field_n = 16
    elif array_kind == "actors":
        new_id = ct.actor_id_enum.strip() if ct.actor_id_enum else ""
        field_n = 0
    elif array_kind == "sprites":
        new_id = ct.sprite_index_enum.strip() if ct.sprite_index_enum else ""
        field_n = 1
    elif array_kind == "collectables":
        new_id = ct.collectable_id_enum.strip() if ct.collectable_id_enum else ""
        field_n = 0
    else:
        return raw
    if not new_id or new_id.startswith("_"):
        return raw
    fields = _split_fields(raw)
    if field_n < len(fields) and fields[field_n].strip() == new_id:
        return raw  # unchanged
    return _replace_field(raw, field_n, new_id)


def _obj_room_id(obj) -> int | None:
    """Read room_id from custom property (preferred) or PropertyGroup fallback."""
    v = obj.get("ct_room_id")
    if v is not None:
        return int(v)
    ct = getattr(obj, "ct", None)
    if ct is not None:
        return ct.room_id
    return None


def _collect_room_buckets(land: str, room_id: int, room_variant: str = "") -> dict[str, list]:
    """Gather Blender objects for (land, room_variant, room_id) into per-suffix lists."""
    buckets: dict[str, list] = {
        "objects": [], "actors": [], "collectables": [], "sprites": [],
    }
    for obj in bpy.data.objects:
        if obj.get("ct_land") != land:
            continue
        if _obj_room_id(obj) != room_id:
            continue
        if obj.get("ct_room_variant", "") != room_variant:
            continue
        array_kind = obj.get("ct_array_kind", "")
        if array_kind in buckets:
            buckets[array_kind].append(obj)
    return buckets


def _has_sentinel(entries: list[str], suffix: str = "objects") -> bool:
    """Return True if entries already contain a null-terminator for this array type.
      objects:           first field is {0,0,0} (pos) : check first brace block
      actors/collectables: start with scalar id 0/ACTOR_NULL then {0,0,0} pos
      sprites:           start with -1 (null sprite type)
    """
    for e in entries:
        s = e.strip()
        if suffix == "objects":
            first = s.split(",")[0].strip() if "," in s else s
            if re.match(r"\{0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\}", first):
                return True
        elif suffix in ("actors", "collectables"):
            if re.match(r"(?:ACTOR_NULL|0)\s*,\s*\{0(?:\.0+)?", s):
                return True
        elif suffix == "sprites":
            if re.match(r"-1\s*,", s):
                return True
    return False


def emit_room_arrays_for_mod(land: str, room_id: int, room_variant: str = "") -> dict[str, str]:
    """Return {suffix: verbatim_c_array_text} for every non-empty array in the room."""
    buckets = _collect_room_buckets(land, room_id, room_variant)
    result: dict[str, str] = {}

    for suffix, objs in buckets.items():
        if not objs:
            continue
        struct = _STRUCT_TYPE[suffix]
        sym = f"{land}_{room_variant}room{room_id}_{suffix}"
        entries: list[str] = []
        for obj in objs:
            array_kind = obj.get("ct_array_kind", "")
            if array_kind or obj.get("ct_raw_entry"):
                entry = _entry_for_obj(obj)
            else:
                # CT_KINDS object with no ct_raw_entry -> generate from properties.
                entry = _entry_for_new_object(obj)
            if entry:
                entries.append(entry)

        if not entries:
            continue

        # Append null sentinel if no imported sentinel is already present.
        sentinel = _NULL_SENTINELS.get(suffix, "")
        if sentinel and not _has_sentinel(entries, suffix):
            entries.append(sentinel)

        lines = [f"{struct} {sym}[] = {{\n"]
        for e in entries:
            lines.append(f"    {{{e}}},\n")
        lines.append("};\n")
        result[suffix] = "".join(lines)

    return result


def emit_room_arrays(land: str, room_id: int) -> str:
    """Walk the current scene for Empties tagged to (land, room_id) and return
    a C source string containing the four re-emitted array declarations.
    Returns an empty string if no matching objects are found.
    """
    buckets: dict[str, list[bpy.types.Object]] = {
        "objects": [], "actors": [], "collectables": [], "sprites": [],
    }
    for obj in bpy.data.objects:
        if obj.get("ct_land") != land:
            continue
        if _obj_room_id(obj) != room_id:
            continue
        kind = obj.get("ct_array_kind", "")
        if kind in buckets:
            buckets[kind].append(obj)

    if all(len(v) == 0 for v in buckets.values()):
        return ""

    lines: list[str] = [
        f"/* CT level editor round-trip export : {land} room {room_id} */\n\n"
    ]
    for suffix, objs in buckets.items():
        struct = _STRUCT_TYPE[suffix]
        sym = f"{land}_room{room_id}_{suffix}"
        lines.append(f"{struct} {sym}[] = {{\n")
        for obj in objs:
            entry = _entry_for_obj(obj)
            lines.append(f"    {{{entry}}},\n")
        lines.append("};\n\n")

    return "".join(lines)


def write_room_export(land: str, room_id: int, out_path: Path) -> int:
    """Write re-emitted arrays to out_path. Returns total entry count."""
    text = emit_room_arrays(land, room_id)
    if not text:
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    total = sum(
        len([o for o in bpy.data.objects
             if o.get("ct_land") == land
             and _obj_room_id(o) == room_id
             and o.get("ct_array_kind") == k])
        for k in ("objects", "actors", "collectables", "sprites")
    )
    return total
