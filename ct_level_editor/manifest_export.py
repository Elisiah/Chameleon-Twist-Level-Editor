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


_VANILLA_LIKE = _re.compile(r"^\d+$")


def _appended_model_symbol(ct) -> str:
    """Resolve the C symbol a mesh wants registered as an appended StageModel.

    Prefer the explicit `model_id_override`; fall back to `model_name` so that
    the rename-vanilla-to-replace workflow works regardless of which UI field
    the user filled in. Returns "" when the value is empty, looks like a
    vanilla reference (numeric / ENUM-style), or is a Gfx-suffixed name.
    """
    candidate = (getattr(ct, "model_id_override", "") or "").strip()
    if not candidate:
        candidate = (getattr(ct, "model_name", "") or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("_") or candidate.endswith("_MODEL"):
        return ""
    if _VANILLA_LIKE.match(candidate):
        return ""
    return candidate


def _resolve_visual_gfx_reference(obj, sym: str, asset_dir: Path) -> dict:
    """Pick the right gfx pointer for an appended visual model.

    Three cases, in priority order:
      1. fast64 has already emitted `model.inc.c` in the asset dir -> use the
         generated symbol; codegen will `#include` it.
      2. The object came from a vanilla import (has `ct_original_model_sym`) and
         the user has not re-exported the gfx -> alias the new `<sym>_Gfx` to
         the original vanilla `<orig>_Gfx`. Lets "rename-to-replace" round-trip
         without forcing a fast64 export when geometry was not edited.
      3. Neither -> emit an extern declaration only; codegen will warn at link
         time. Caller must run "Export Gfx" before the build will succeed.
    """
    model_inc = asset_dir / "model.inc.c"
    if model_inc.exists():
        mesh_name = obj.data.name if obj.data else obj.name
        mesh_sym = "".join(c if c.isalnum() else "_" for c in mesh_name)
        return {"gfx_entry": f"{sym}_{mesh_sym}_mesh"}

    original = (obj.get("ct_original_model_sym") or "").strip()
    if original and original != sym:
        return {"gfx_alias": original}

    return {}


def _register_appended_visual_model(
    obj,
    sym: str,
    land: str,
    manifest_dir: Path,
    appended_models: list[dict],
    appended_model_names: set[str],
    collision_summaries: list[dict],
) -> bool:
    """Emit the collision asset for an appended visual mesh and record the
    manifest entry. Returns True if the model was registered.
    """
    asset_dir = manifest_dir / sym
    collision_path = asset_dir / f"{sym}.collision.c"
    try:
        summary = collision_export.emit_collision(obj, sym, collision_path)
    except Exception as e:
        # Surface, do not swallow: this is the difference between "silently
        # produces no enum entry" and a diagnosable user error.
        print(f"[CT] collision export failed for {sym!r} on {obj.name!r}: {e}")
        return False

    entry: dict = {"name": sym, "collision": f"{sym}/{sym}"}
    entry.update(_resolve_visual_gfx_reference(obj, sym, asset_dir))

    appended_models.append(entry)
    appended_model_names.add(sym)
    collision_summaries.append({**summary, "name": sym})
    return True


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

        if obj.type == "MESH" and not ct.is_collision:
            sym = _appended_model_symbol(ct)
            if sym and sym not in appended_model_names:
                _register_appended_visual_model(
                    obj, sym, land, manifest_dir,
                    appended_models, appended_model_names, collision_summaries,
                )
            if not (kind_def and kind_def.aux_array):
                continue

        if obj.type == "MESH" and ct.is_collision:
            sym = _appended_model_symbol(ct) or _sanitize_symbol(obj.name, land)
            asset_dir = manifest_dir / sym
            collision_path = asset_dir / f"{sym}.collision.c"
            summary = collision_export.emit_collision(obj, sym, collision_path)
            collision_summaries.append({**summary, "name": sym})
            if sym not in appended_model_names:
                appended_model_names.add(sym)
                entry: dict = {"name": sym, "collision": f"{sym}/{sym}"}
                entry.update(_resolve_visual_gfx_reference(obj, sym, asset_dir))
                appended_models.append(entry)
            if ct.kind not in kinds.KIND_REGISTRY_BY_ID:
                continue

        if obj.get("ct_array_kind") and not (kind_def and kind_def.aux_array):
            continue

        if ct.kind not in kinds.KIND_REGISTRY_BY_ID:
            continue
        appended_sym = _appended_model_symbol(ct)
        if obj.type == "MESH" and not appended_sym and not (kind_def and kind_def.aux_array):
            continue

        room_variant = obj.get("ct_room_variant", "")
        room_key = f"{room_variant}{ct.room_id}"
        room = rooms.setdefault(room_key, {"objects": {"append": []}})

        model_sym = appended_sym
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
