"""PropertyGroups attached to Object and Scene."""

import re
from pathlib import Path

import bpy
from . import kinds

GAME_FPS = 30


def _read_land_source(context) -> str | None:
    """Read the contents of the current Land's .c file, if available."""
    if not context:
        return None
    scene = context.scene
    repo_root = bpy.path.abspath(getattr(getattr(scene, "ct", None), "repo_root", None) or "")
    land = getattr(getattr(scene, "ct", None), "land", None) or ""
    if not repo_root or not land:
        return None
    land_c = Path(repo_root) / "src" / "levelGroup" / f"{land}.c"
    return land_c.read_text() if land_c.exists() else None


def _read_enums_h(context) -> str | None:
    if not context:
        return None
    repo_root = bpy.path.abspath(getattr(getattr(context.scene, "ct", None), "repo_root", None) or "")
    if not repo_root:
        return None
    enums_h = Path(repo_root) / "include" / "enums.h"
    return enums_h.read_text() if enums_h.exists() else None


def _parse_c_enum(body: str) -> list[tuple[str, str, str]]:
    items = []
    val = 0
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("//") or line.startswith("#") or line.startswith("/*"):
            continue
        if "=" in line:
            name, rhs = line.split("=", 1)
            name = name.strip()
            try:
                val = int(rhs.strip())
            except ValueError:
                pass
        else:
            name = line.strip()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            items.append((name, name, f"id={val}"))
            val += 1
    return items


_model_items_cache: list = [("_none", "(set Repo Root + Land)", "")]
_actor_items_cache: list = [("ACTOR_NULL", "ACTOR_NULL", "id=0")]
_sprite_items_cache: list = [("SPRITE_BLANK", "SPRITE_BLANK", "id=0")]


def get_model_items(self, context) -> list:
    global _model_items_cache
    try:
        source = _read_land_source(context)
        if source is None:
            return [("_none", "(set Repo Root + Land)", "")]
        from . import room_import
        enum_map = room_import.parse_model_enum(source)
        items = [(name, name, f"index={val}") for name, val in sorted(enum_map.items(), key=lambda x: x[1])]
        _model_items_cache = [("_none", "(none)", "")] + items
    except Exception:
        pass
    return _model_items_cache


def get_actor_items(self, context) -> list:
    global _actor_items_cache
    try:
        source = _read_enums_h(context)
        if source is None:
            return _actor_items_cache
        m = re.search(r"enum actorIDs\s*\{([^}]+)\}", source, re.DOTALL)
        if m:
            items = _parse_c_enum(m.group(1))
            if items:
                _actor_items_cache = items
    except Exception:
        pass
    return _actor_items_cache


def get_sprite_items(self, context) -> list:
    global _sprite_items_cache
    try:
        source = _read_enums_h(context)
        if source is None:
            return _sprite_items_cache
        m = re.search(r"enum SPRITE\s*\{([^}]+)\}", source, re.DOTALL)
        if m:
            items = _parse_c_enum(m.group(1))
            if items:
                _sprite_items_cache = items
    except Exception:
        pass
    return _sprite_items_cache


def _sync_room_id(self, context):
    """Keep the object's custom property 'ct_room_id' in sync with the UI property."""
    if context and context.active_object:
        context.active_object["ct_room_id"] = self.room_id


def _kind_update(self, context):
    """When the Kind enum changes, set default field values on the active object
    for all fields defined by the new kind.
    """
    obj = context.active_object
    if not obj:
        return
    kind_def = kinds.KIND_REGISTRY_BY_ID.get(self.kind)
    if not kind_def:
        return
    for f in kind_def.fields:
        prop_name = f"ct_field_{f.c_name}"
        if prop_name not in obj:
            if f.field_type == "int":
                obj[prop_name] = int(f.default)
            elif f.field_type == "float":
                obj[prop_name] = float(f.default)
            else:
                obj[prop_name] = f.default


def _update_platform_times(self, context, is_time1):
    """Translate platform time-in-seconds back to the C field (frames) and store it.
    is_time1==True -> noKeyframes; False -> unk44 (time to return).
    """
    obj = context.active_object
    if not obj or obj.ct.kind != "moving_platform_linear":
        return
    if is_time1:
        obj["ct_field_noKeyframes"] = int(round(self.moving_platform_time1_sec * GAME_FPS))
    else:
        obj["ct_field_unk44"] = int(round(self.moving_platform_time2_sec * GAME_FPS))


def _bl_to_ct(loc) -> tuple[float, float, float]:
    return (loc[0], loc[2], -loc[1])


def location_keyframes(obj) -> list[tuple[float, tuple[float, float, float]]]:
    """Return [(frame, (bx,by,bz)), ...] sampled at every unique key on
    `obj.location` F-curves. Sorted by frame."""
    if not obj.animation_data or not obj.animation_data.action:
        return []
    fc_x = fc_y = fc_z = None
    for fc in obj.animation_data.action.fcurves:
        if fc.data_path != "location":
            continue
        if fc.array_index == 0: fc_x = fc
        elif fc.array_index == 1: fc_y = fc
        elif fc.array_index == 2: fc_z = fc
    frames: set[int] = set()
    for fc in (fc_x, fc_y, fc_z):
        if fc is None:
            continue
        for kp in fc.keyframe_points:
            frames.add(int(round(kp.co[0])))
    out = []
    for f in sorted(frames):
        x = fc_x.evaluate(f) if fc_x else obj.location.x
        y = fc_y.evaluate(f) if fc_y else obj.location.y
        z = fc_z.evaluate(f) if fc_z else obj.location.z
        out.append((float(f), (x, y, z)))
    return out


class CTObjectProps(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(
        name="Kind", items=kinds.CT_KINDS, default="static_mesh",
        description="Struct variant – determines dispatch, available fields, and defaults",
        update=_kind_update)
    room_id: bpy.props.IntProperty(
        name="Room ID", default=0, min=0, update=_sync_room_id)
    model_name: bpy.props.StringProperty(name="Model Symbol", default="")
    is_collision: bpy.props.BoolProperty(name="Is Collision Mesh", default=False)
    pole_auto_height: bpy.props.BoolProperty(name="Auto Update Height", default=True)

    model_enum: bpy.props.EnumProperty(name="Model", items=get_model_items)
    model_id_override: bpy.props.StringProperty(name="Model ID Override", default="")
    actor_id_enum: bpy.props.EnumProperty(name="Actor ID", items=get_actor_items)
    sprite_index_enum: bpy.props.EnumProperty(name="Sprite", items=get_sprite_items)
    collectable_id_enum: bpy.props.EnumProperty(name="Collectable", items=get_actor_items)

    moving_platform_time1_sec: bpy.props.FloatProperty(
        name="Time to Target (s)", default=0.0, min=0.0,
        update=lambda self, ctx: _update_platform_times(self, ctx, is_time1=True))
    moving_platform_time2_sec: bpy.props.FloatProperty(
        name="Time to Return (s)", default=0.0, min=0.0,
        update=lambda self, ctx: _update_platform_times(self, ctx, is_time1=False))


class CTSceneProps(bpy.types.PropertyGroup):
    land: bpy.props.StringProperty(name="Land", default="AntLand")
    repo_root: bpy.props.StringProperty(name="Repo Root", subtype='DIR_PATH', default="")
    f3d_preset: bpy.props.StringProperty(name="F3D Preset", default="")


def _sync_linear_platform(obj):
    keys = location_keyframes(obj)
    if len(keys) < 2:
        return
    f0, _ = keys[0]
    f1, p1 = keys[1]
    target = _bl_to_ct(p1)
    travel = max(1, int(round(f1 - f0)))
    sec = travel / GAME_FPS

    changed = False
    if abs(float(obj.get("ct_field_unk28", 0.0)) - target[0]) > 1e-3:
        obj["ct_field_unk28"] = float(target[0]); changed = True
    if abs(float(obj.get("ct_field_unk2C", 0.0)) - target[1]) > 1e-3:
        obj["ct_field_unk2C"] = float(target[1]); changed = True
    if abs(float(obj.get("ct_field_unk30", 0.0)) - target[2]) > 1e-3:
        obj["ct_field_unk30"] = float(target[2]); changed = True
    if int(obj.get("ct_field_noKeyframes", 0)) != travel:
        obj["ct_field_noKeyframes"] = travel; changed = True
    if changed and abs(obj.ct.moving_platform_time1_sec - sec) > 1e-4:
        obj.ct["moving_platform_time1_sec"] = sec


def _sync_keyframed_platform(obj):
    keys = location_keyframes(obj)
    n = len(keys)
    holds = list(obj.get("ct_keyframe_holds", []))
    if len(holds) != n:
        if len(holds) < n:
            holds = holds + [0] * (n - len(holds))
        else:
            holds = holds[:n]
        obj["ct_keyframe_holds"] = holds


@bpy.app.handlers.persistent
def _ct_depsgraph_handler(scene):
    for obj in bpy.data.objects:
        if not hasattr(obj, "ct"):
            continue
        ct = obj.ct
        if ct.kind == 'pole_grabbable' and ct.pole_auto_height:
            if "ct_field_grab_line" in obj:
                new_height = round(obj.dimensions.z, 4)
                if new_height >= 0.001:
                    current = float(obj["ct_field_grab_line"])
                    if abs(current - new_height) > 0.0001:
                        obj["ct_field_grab_line"] = new_height
        elif ct.kind == "moving_platform_linear":
            _sync_linear_platform(obj)
        elif ct.kind == "platform_keyframed":
            _sync_keyframed_platform(obj)


CLASSES = (CTObjectProps, CTSceneProps)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.ct = bpy.props.PointerProperty(type=CTObjectProps)
    bpy.types.Scene.ct = bpy.props.PointerProperty(type=CTSceneProps)
    bpy.app.handlers.depsgraph_update_post.append(_ct_depsgraph_handler)


def unregister():
    if _ct_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_ct_depsgraph_handler)
    del bpy.types.Scene.ct
    del bpy.types.Object.ct
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
