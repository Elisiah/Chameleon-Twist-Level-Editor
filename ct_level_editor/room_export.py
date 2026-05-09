"""Round-trip exporter: re-emit vanilla room arrays from Blender scene Empties."""

from __future__ import annotations
import re
from pathlib import Path
import bpy

from .room_import import split_top_level_fields as _split_fields
from . import kinds

_IS_VANILLA_ID = re.compile(r"^\d+$")


def _model_name_to_define(name: str) -> str:
    return name.upper() + "_MODEL"


def _blender_to_ct(loc) -> tuple[float, float, float]:
    x, y, z = loc
    return (x, z, -y)


def _blender_scale_to_ct(scale) -> tuple[float, float, float]:
    bx, by, bz = scale
    return (bx, bz, by)


def _replace_field(raw: str, n: int, new_text: str) -> str:
    fields = _split_fields(raw)
    if n >= len(fields):
        return raw
    fields[n] = new_text
    return ", ".join(fields)


def _format_vec3(v: tuple[float, float, float]) -> str:
    return "{" + ", ".join(f"{c:.4f}" for c in v) + "}"


_POS_FIELD_INDEX = {
    "objects":      0,
    "actors":       0,
    "collectables": 0,
    "sprites":      0,
}

_STRUCT_TYPE = {
    "objects":      "RoomObject",
    "actors":       "RoomActor",
    "collectables": "Collectable",
    "sprites":      "SpriteActor",
}


def _replace_nth_vec3(raw: str, n: int, new_vec: tuple[float, float, float]) -> str:
    count = 0
    i = 0
    while i < len(raw):
        if raw[i] != "{":
            i += 1
            continue
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
        if len(parts) == 3:
            def _is_num(s: str) -> bool:
                return bool(re.match(r"^-?\d+(\.\d+)?(f|F)?$", s.strip()))
            if all(_is_num(p) for p in parts):
                if count == n:
                    return raw[:i] + _format_vec3(new_vec) + raw[j:]
                count += 1
        i = j
    return raw


_MOVE_THRESHOLD = 0.001
_SCALE_THRESHOLD = 0.0001
_ROTATION_THRESHOLD = 1e-6

_ACTOR_TEMPLATE = (
    "ACTOR_NULL, {0.0, 0.0, 0.0}, "
    "0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0, "
    "0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0.0, 0.0, 0, 0"
)
_COLLECTABLE_TEMPLATE = "0, {0.0, 0.0, 0.0}, 4294967295, 0, 0, 0"

_NULL_SENTINELS = {
    "objects":      "{0.0,0.0,0.0}, {0.0,0.0,0.0}, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL, NULL, 0, 0, 0, 0, 0, 0, 0, 0, 0",
    "actors":       "0, {0.0,0.0,0.0}, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0",
    "collectables": "0, {0.0,0.0,0.0}, 4294967295, 0, 0, 0",
    "sprites":      "-1, 0, {-1.0,-1.0,-1.0}, {-1.0,32.0,32.0}, 1, 0, 0.0, 0, 0, 0, 0, 0, {-1, -1, 0, 0}",
}


def _entry_for_new_object(obj: bpy.types.Object) -> str:
    ct = getattr(obj, "ct", None)
    if ct is None:
        return ""

    kind_def = kinds.KIND_REGISTRY_BY_ID.get(ct.kind)
    if not kind_def:
        return ""

    pos = _blender_to_ct(obj.location)
    scale = _blender_scale_to_ct(obj.scale)

    model = ""
    if ct:
        model = ct.model_enum.strip() if ct.model_enum else ct.model_name.strip()
    if not model:
        model = "0"

    pos_s = f"{{{pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}}}"
    scale_s = f"{{{scale[0]:.4f}, {scale[1]:.4f}, {scale[2]:.4f}}}"

    euler = obj.rotation_euler.copy()
    rot_thresh = 0.0001
    axis = 0
    angle_rad = 0.0
    if abs(euler.x) > rot_thresh:
        axis = 1; angle_rad = euler.x
    elif abs(euler.z) > rot_thresh:
        axis = 2; angle_rad = euler.z
    elif abs(euler.y) > rot_thresh:
        axis = 3; angle_rad = -euler.y

    FIELD_COUNT = 31
    fields = ["0"] * FIELD_COUNT
    fields[0] = pos_s
    fields[1] = scale_s
    fields[2] = str(axis)
    fields[3] = f"{angle_rad:.6f}f"
    fields[16] = model
    fields[24] = f"0x{kind_def.dispatch:02X}"

    for f in kind_def.fields:
        if not f.in_struct:
            continue
        if f.c_index is not None and 0 <= f.c_index < FIELD_COUNT:
            prop_name = f"ct_field_{f.c_name}"
            val = obj.get(prop_name, f.default)
            fields[f.c_index] = str(val)

    for idx, val in kind_def.ro_overrides.items():
        if 0 <= idx < FIELD_COUNT:
            fields[idx] = val

    return ", ".join(fields)


def _entry_for_obj(obj: bpy.types.Object) -> str:
    raw: str = obj.get("ct_raw_entry", "")
    array_kind: str = obj.get("ct_array_kind", "objects")

    if not raw:
        if array_kind == "actors":
            raw = _ACTOR_TEMPLATE
        elif array_kind == "collectables":
            raw = _COLLECTABLE_TEMPLATE
        else:
            return ""

    original_pos = obj.get("ct_original_pos")
    ct_pos = _blender_to_ct(obj.location)
    if original_pos is not None:
        moved = sum(abs(ct_pos[i] - original_pos[i]) for i in range(3)) > _MOVE_THRESHOLD
    else:
        moved = True
    if moved:
        raw = _replace_nth_vec3(raw, _POS_FIELD_INDEX.get(array_kind, 0), ct_pos)

    if array_kind == "objects":
        original_scale = obj.get("ct_original_scale")
        ct_scale = _blender_scale_to_ct(obj.scale)
        if original_scale is not None:
            rescaled = sum(abs(ct_scale[i] - original_scale[i]) for i in range(3)) > _SCALE_THRESHOLD
        else:
            rescaled = True
        if rescaled:
            raw = _replace_nth_vec3(raw, 1, ct_scale)

        orig_axis = obj.get("ct_original_axis")
        orig_angle = obj.get("ct_original_angle_rad")
        euler = obj.rotation_euler.copy()
        thresh = 0.0001
        new_axis = 0
        new_angle = 0.0
        if abs(euler.x) > thresh:
            new_axis = 1; new_angle = euler.x
        elif abs(euler.z) > thresh:
            new_axis = 2; new_angle = euler.z
        elif abs(euler.y) > thresh:
            new_axis = 3; new_angle = -euler.y

        if orig_axis is None or new_axis != orig_axis or abs(new_angle - (orig_angle or 0.0)) > _ROTATION_THRESHOLD:
            raw = _replace_field(raw, 2, str(new_axis))
            raw = _replace_field(raw, 3, f"{new_angle:.6f}f")

    raw = _maybe_patch_id(obj, array_kind, raw)
    raw = _patch_kind_fields(obj, array_kind, raw)
    return raw


def _patch_kind_fields(obj, array_kind: str, raw: str) -> str:
    if array_kind != "objects":
        return raw
    ct = getattr(obj, "ct", None)
    if ct is None:
        return raw
    kind_def = kinds.KIND_REGISTRY_BY_ID.get(ct.kind)
    if kind_def is None or kind_def.aux_array:
        return raw
    for f in kind_def.fields:
        if not f.in_struct or f.c_index is None:
            continue
        prop_name = f"ct_field_{f.c_name}"
        if prop_name not in obj:
            continue
        val = obj[prop_name]
        if f.field_type == "float":
            text = f"{float(val):.4f}"
        else:
            text = str(val)
        raw = _replace_field(raw, f.c_index, text)
    for idx, val in kind_def.ro_overrides.items():
        raw = _replace_field(raw, idx, val)
    return raw


def _maybe_patch_id(obj: bpy.types.Object, array_kind: str, raw: str) -> str:
    ct = getattr(obj, "ct", None)
    if ct is None:
        return raw
    if array_kind == "objects":
        override = getattr(ct, "model_id_override", "").strip()
        if override and not override.startswith("_"):
            if not _IS_VANILLA_ID.match(override) and not override.endswith("_MODEL"):
                new_id = _model_name_to_define(override)
            else:
                new_id = override
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
        return raw
    return _replace_field(raw, field_n, new_id)


def _obj_room_id(obj) -> int | None:
    v = obj.get("ct_room_id")
    if v is not None:
        return int(v)
    ct = getattr(obj, "ct", None)
    if ct is not None:
        return ct.room_id
    return None


def _collect_room_buckets(land: str, room_id: int, room_variant: str = "") -> dict[str, list]:
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
        if array_kind not in buckets:
            continue
        ct = getattr(obj, "ct", None)
        kind_def = kinds.KIND_REGISTRY_BY_ID.get(ct.kind) if ct else None
        if kind_def and kind_def.aux_array:
            continue  # routed through manifest_export's append path
        buckets[array_kind].append(obj)
    return buckets


def _has_sentinel(entries: list[str], suffix: str = "objects") -> bool:
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
                entry = _entry_for_new_object(obj)
            if entry:
                entries.append(entry)

        if not entries:
            continue

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
    buckets: dict[str, list] = {
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