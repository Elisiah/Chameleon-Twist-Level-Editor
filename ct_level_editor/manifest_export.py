"""Walk the scene -> write tools/LevelEditor/manifests/<Land>/<Land>_mod.json
and any per-mesh collision .collision.c files alongside it."""

import bpy
import json
from pathlib import Path

import re as _re

from . import collision_export
from .properties import CT_KINDS

KIND_NAMES = {k for (k, _, _) in CT_KINDS}


def _vec3(co) -> list[float]:
    return [round(co[0], 4), round(co[2], 4), round(-co[1], 4)]


def _scale3(s) -> list[float]:
    # Scale doesn't get axis-negated.
    return [round(s[0], 4), round(s[2], 4), round(s[1], 4)]


def _sanitize_symbol(name: str, land: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in name)
    if not cleaned[:1].isalpha():
        cleaned = f"{land}_{cleaned}"
    return cleaned


def export_scene(scene: bpy.types.Scene, manifest_dir: Path) -> dict:
    """Returns a summary dict suitable for showing in a Blender info popup."""
    land = scene.ct.land or "AntLand"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    appended_models: list[dict] = []
    appended_model_names: set[str] = set()
    rooms: dict[str, dict] = {}
    collision_summaries: list[dict] = []

    for obj in scene.objects:
        ct = obj.ct

        # Imported mesh objects whose geometry was edited and given a new model name.
        # The override is a new asset name (not a vanilla define like FOO_MODEL or a raw int).
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
                        # fast64 names the display list {DLName}_{toAlnum(ObjName)}_mesh
                        _gfx_obj = "".join(c if c.isalnum() else "_" for c in obj.name)
                        appended_models.append({
                            "name": override,
                            "collision": f"{override}/{override}",
                            "gfx_entry": f"{override}_{_gfx_obj}_mesh",
                        })
                    except Exception as _exc:
                        pass  # no geometry (placeholder empty); skip
            continue  # placement data handled by room_export raw_replace

        # Collision meshes -> emit asset, register a stageModels.append entry.
        if obj.type == "MESH" and ct.is_collision:
            sym = ct.model_name or _sanitize_symbol(obj.name, land)
            asset_dir = manifest_dir / sym
            collision_path = asset_dir / f"{sym}.collision.c"
            summary = collision_export.emit_collision(obj, sym, collision_path)
            collision_summaries.append({**summary, "name": sym})
            if sym not in appended_model_names:
                appended_model_names.add(sym)
                appended_models.append({
                    "name": sym,
                    "gfx": f"{sym}/{sym}",   # fast64 user-supplied; same dir
                    "collision": f"{sym}/{sym}",
                })
            continue

        # Skip imported vanilla room entries : they are round-tripped via
        # raw_replace, not objects.append, to avoid duplicating every entry.
        if obj.get("ct_array_kind"):
            continue

        #Skip objects that have no explicit CT kind assigned / untagged meshes.
        if ct.kind not in KIND_NAMES:
            continue
        if obj.type == "MESH" and not ct.model_name:
            continue

        room_variant = obj.get("ct_room_variant", "")
        room_key = f"{room_variant}{ct.room_id}"
        room = rooms.setdefault(room_key, {"objects": {"append": []}})

        # Resolve model: explicit name -> enum dropdown -> sanitised object name.
        model_sym = ct.model_name
        if not model_sym:
            enum_val = getattr(ct, "model_enum", "")
            if enum_val and not enum_val.startswith("_"):
                model_sym = enum_val
        if not model_sym:
            model_sym = _sanitize_symbol(obj.name, land)

        entry: dict = {
            "kind": ct.kind,
            "model": model_sym,
            "pos": _vec3(obj.location),
            "scale": _scale3(obj.scale),
        }
        if ct.kind == "exit_trigger":
            entry["direction"] = ct.exit_direction
            entry["target_arg"] = ct.exit_target_arg
        elif ct.kind == "pole_grabbable":
            entry["grab_line"] = round(ct.grab_line, 4)

        # TODO: Other kind-specific extras get filled in as the schema grows
        # (keyframes for moving_platform, grab_line for pole, etc.).

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