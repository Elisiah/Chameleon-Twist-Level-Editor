"""Walk the scene -> write tools/LevelEditor/manifests/<Land>/<Land>_mod.json
and any per-mesh collision .collision.c files alongside it."""

import bpy
import json
from pathlib import Path
import re as _re

from . import collision_export, kinds, properties


def _vec3(co) -> list[float]:
    """Convert a Blender location to CT coordinate list (X, Z, -Y), rounded to 4 decimals."""
    return [round(co[0], 4), round(co[2], 4), round(-co[1], 4)]


def _scale3(s) -> list[float]:
    """Convert a Blender scale to CT scale list (sx, sz, sy), rounded to 4 decimals."""
    return [round(s[0], 4), round(s[2], 4), round(s[1], 4)]


def _sanitize_symbol(name: str, land: str) -> str:
    """Clean a Blender object name into a valid C identifier.
    If the name doesn't start with a letter, prepend the land name.
    """
    cleaned = "".join(c if c.isalnum() else "_" for c in name)
    if not cleaned[:1].isalpha():
        cleaned = f"{land}_{cleaned}"
    return cleaned


def _sample_keyframes_for_export(obj) -> list[dict]:
    """For a `platform_keyframed` kind, read the object's location F-curves and
    pair each waypoint with its `ct_keyframe_holds[i]` scalar.
    Returns a list of dicts: {'pos': [x,y,z], 'hold_frames': int, 'flags': [0,0,0,0,0]}
    """
    keys = properties.location_keyframes(obj)
    if not keys:
        return []
    holds = list(obj.get("ct_keyframe_holds", []))
    out: list[dict] = []
    for i, (_frame, p) in enumerate(keys):
        ct_p = [round(p[0], 4), round(p[2], 4), round(-p[1], 4)]
        hold = int(holds[i]) if i < len(holds) else 0
        out.append({"pos": ct_p, "hold_frames": hold, "flags": [0, 0, 0, 0, 0]})
    return out


def _resync_linear_fields(obj) -> None:
    """Sync linear platform C fields (target pos/travel frames) from the object's animation."""
    properties._sync_linear_platform(obj)


def export_scene(scene: bpy.types.Scene, manifest_dir: Path) -> dict:
    """Walk the scene, collect CT placements, write <Land>_mod.json and collision assets.
    Returns a summary dict with manifest_path, collision_assets, object_count, appended_model_count.
    """
    land = scene.ct.land or "AntLand"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    appended_models: list[dict] = []
    appended_model_names: set[str] = set()
    rooms: dict[str, dict] = {}
    collision_summaries: list[dict] = []

    for obj in scene.objects:
        ct = obj.ct
        kind_def = kinds.KIND_REGISTRY_BY_ID.get(ct.kind)

        if obj.type == "MESH" and obj.get("ct_array_kind") == "objects":
            override = getattr(ct, "model_id_override", "").strip()
            if override and not override.startswith("_") and not _re.match(r"^\d+$", override) and not override.endswith("_MODEL"):
                if override not in appended_model_names:
                    asset_dir = manifest_dir / override
                    collision_path = asset_dir / f"{override}.collision.c"
                    try:
                        summary = collision_export.emit_collision_modelspace(obj, override, collision_path)
                        collision_summaries.append({**summary, "name": override})
                        appended_model_names.add(override)
                        _gfx_obj = "".join(c if c.isalnum() else "_" for c in obj.name)
                        appended_models.append({
                            "name": override,
                            "collision": f"{override}/{override}",
                            "gfx_entry": f"{override}_{_gfx_obj}_mesh",
                        })
                    except Exception:
                        pass
            if not (kind_def and kind_def.aux_array):
                continue

        if obj.type == "MESH" and ct.is_collision:
            sym = ct.model_name or _sanitize_symbol(obj.name, land)
            asset_dir = manifest_dir / sym
            collision_path = asset_dir / f"{sym}.collision.c"
            summary = collision_export.emit_collision(obj, sym, collision_path)
            collision_summaries.append({**summary, "name": sym})
            if sym not in appended_model_names:
                appended_model_names.add(sym)
                _gfx_obj = "".join(c if c.isalnum() else "_" for c in obj.name)
                appended_models.append({
                    "name": sym,
                    "collision": f"{sym}/{sym}",
                    "gfx_entry": f"{sym}_{_gfx_obj}_mesh",
                })
            if ct.kind not in kinds.KIND_REGISTRY_BY_ID:
                continue

        if obj.get("ct_array_kind") and not (kind_def and kind_def.aux_array):
            continue

        if ct.kind not in kinds.KIND_REGISTRY_BY_ID:
            continue
        if obj.type == "MESH" and not ct.model_name and not (kind_def and kind_def.aux_array):
            continue

        room_variant = obj.get("ct_room_variant", "")
        room_key = f"{room_variant}{ct.room_id}"
        room = rooms.setdefault(room_key, {"objects": {"append": []}})

        model_sym = ct.model_name
        if not model_sym:
            enum_val = getattr(ct, "model_enum", "")
            if enum_val and not enum_val.startswith("_"):
                model_sym = enum_val
        if not model_sym:
            model_sym = _sanitize_symbol(obj.name, land)

        if ct.kind == "moving_platform_linear":
            _resync_linear_fields(obj)

        entry = {
            "kind": ct.kind,
            "model": model_sym,
            "pos": _vec3(obj.location),
            "scale": _scale3(obj.scale),
        }

        fields_dict = {}
        for f in kind_def.fields:
            prop_name = f"ct_field_{f.c_name}"
            val = obj.get(prop_name, f.default)
            if f.in_struct:
                fields_dict[f.c_name] = str(val)
            else:
                entry[f.manifest_key or f.c_name] = val

        if fields_dict:
            entry["fields"] = fields_dict

        if kind_def.aux_array:
            kfs = _sample_keyframes_for_export(obj)
            entry["keyframes"] = kfs
            entry["name"] = _sanitize_symbol(obj.name, land)
            if kfs:
                entry["pos"] = kfs[0]["pos"]

        room["objects"]["append"].append(entry)

    manifest = {
        "schema_version": 0,
        "land": land,
        "inherit": "vanilla",
        "stageModels": {"append": appended_models},
        "rooms": rooms,
    }
    manifest_path = manifest_dir / f"{land}_mod.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    return {
        "manifest_path": str(manifest_path),
        "collision_assets": collision_summaries,
        "object_count": sum(len(r["objects"]["append"]) for r in rooms.values()),
        "appended_model_count": len(appended_models),
    }
