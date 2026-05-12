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


def _set_kf_return_sec(self, value):
    obj = self.id_data
    holds = list(obj.get("ct_keyframe_holds", []))
    if not holds:
        return
    holds[-1] = int(round(value * GAME_FPS))
    obj["ct_keyframe_holds"] = holds


def _sync_room_id(self, context):
    """Keep the object's custom property 'ct_room_id' in sync with the UI property."""
    if context and context.active_object:
        context.active_object["ct_room_id"] = self.room_id


def _flip_mesh_normals(obj):
    """Flip every face normal once. Marks `ct_skybox_normals_flipped` so re-assigning
    the skybox kind doesn't double-flip back to outward-facing."""
    if obj.type != "MESH" or obj.data is None or obj.mode == "EDIT":
        return
    if obj.get("ct_skybox_normals_flipped"):
        return
    import bmesh
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    for f in bm.faces:
        f.normal_flip()
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    me.update()
    obj["ct_skybox_normals_flipped"] = 1


PLATFORM_KINDS = frozenset({"moving_platform_linear", "platform_keyframed"})
SYNC_KINDS = PLATFORM_KINDS | {"pole_grabbable"}


def _apply_kind_transform_locks(obj, kind: str) -> None:
    """Platforms only animate location: lock rotation+scale so Blender's
    Separate / accidental keyframe authoring on those channels can't inject
    values the engine doesn't honor (and that mid-operator depsgraph passes
    used to snapshot as truth)."""
    locked = (True, True, True)
    if kind in PLATFORM_KINDS:
        if tuple(obj.lock_rotation) != locked:
            obj.lock_rotation = locked
        if tuple(obj.lock_scale) != locked:
            obj.lock_scale = locked


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
    if self.kind == "skybox":
        _flip_mesh_normals(obj)
    _apply_kind_transform_locks(obj, self.kind)


def _update_platform_times(self, context, is_time1):
    """Translate platform time-in-seconds back to the C field (frames) and store it.
    is_time1==True -> noKeyframes; False -> unk44 (time to return).

    Reads `self.id_data` rather than `context.active_object` so the write lands
    on the object that owns the property regardless of which object happens to
    be active when the slider is edited (Outliner data-view, scripted edits,
    multi-selection, etc.).
    """
    obj = self.id_data
    if obj is None or obj.ct.kind != "moving_platform_linear":
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

    keyframed_return_sec: bpy.props.FloatProperty(
        name="Return (s)", min=0.0,
        get=lambda self: (list(self.id_data.get("ct_keyframe_holds", []))[-1] / GAME_FPS)
            if self.id_data.get("ct_keyframe_holds") else 0.0,
        set=lambda self, v: _set_kf_return_sec(self, v))


class CTSceneProps(bpy.types.PropertyGroup):
    land: bpy.props.StringProperty(name="Land", default="AntLand")
    repo_root: bpy.props.StringProperty(name="Repo Root", subtype='DIR_PATH', default="")
    f3d_preset: bpy.props.StringProperty(name="F3D Preset", default="")
    # JSON list of {"variant": str, "room_id": int} entries: every room the user
    # has imported in this .blend. Persists with the file so deleting the last
    # vanilla object from a room still forces a raw_replace override on export
    # (otherwise codegen would fall back to the vanilla section verbatim).
    imported_rooms_json: bpy.props.StringProperty(default="[]")


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
    """Hold semantics: holds[i] for i<n-1 is travel length to next waypoint
    (synced from f-curve frame deltas). holds[n-1] is the user-set return time."""
    keys = location_keyframes(obj)
    n = len(keys)
    prev = list(obj.get("ct_keyframe_holds", []))
    holds = (prev + [0] * n)[:n] if len(prev) != n else list(prev)
    for i in range(n - 1):
        delta = max(1, int(round(keys[i + 1][0] - keys[i][0])))
        holds[i] = delta
    if holds != prev:
        obj["ct_keyframe_holds"] = holds


_pending_sync: set[str] = set()
_flush_in_progress: bool = False


def _is_sync_candidate(obj) -> bool:
    return hasattr(obj, "ct") and obj.ct.kind in SYNC_KINDS


def _sync_pole_height(obj) -> None:
    """Drive the `ct_field_grab_line` custom prop from the mesh's evaluated Z
    extent. Runs in the deferred flush so `obj.dimensions` is read against a
    fully-resolved depsgraph state, not mid-operator transient geometry."""
    if not obj.ct.pole_auto_height or "ct_field_grab_line" not in obj:
        return
    new_height = round(obj.dimensions.z, 4)
    if new_height < 0.001:
        return
    current = float(obj["ct_field_grab_line"])
    if abs(current - new_height) > 0.0001:
        obj["ct_field_grab_line"] = new_height


def _flush_pending_sync():
    """Drain `_pending_sync` and run each object's kind-specific sync. Runs as
    a one-shot timer callback *after* the depsgraph pass that enqueued it, so
    custom-property writes don't get folded back into the same update phase
    (which is what caused Separate to clobber sibling objects)."""
    global _flush_in_progress
    if _flush_in_progress:
        return None
    _flush_in_progress = True
    try:
        names = list(_pending_sync)
        _pending_sync.clear()
        for name in names:
            obj = bpy.data.objects.get(name)
            if obj is None or not _is_sync_candidate(obj):
                continue
            kind = obj.ct.kind
            try:
                _apply_kind_transform_locks(obj, kind)
                if kind == "pole_grabbable":
                    _sync_pole_height(obj)
                elif kind == "moving_platform_linear":
                    _sync_linear_platform(obj)
                elif kind == "platform_keyframed":
                    _sync_keyframed_platform(obj)
            except ReferenceError:
                # Object was deleted between enqueue and flush — fine.
                continue
            except Exception as e:
                print(f"[CT] sync failed for {name!r}: {e}")
    finally:
        _flush_in_progress = False
    return None  # one-shot


def _arm_flush() -> None:
    if not bpy.app.timers.is_registered(_flush_pending_sync):
        bpy.app.timers.register(_flush_pending_sync, first_interval=0.0)


def _action_uses_location(action) -> bool:
    if action is None:
        return False
    for fc in action.fcurves:
        if fc.data_path == "location":
            return True
    return False


@bpy.app.handlers.persistent
def _ct_depsgraph_handler(scene, depsgraph):
    """Enqueue dirty CT objects for deferred sync.

    The previous implementation iterated `bpy.data.objects` on every depsgraph
    pass and wrote custom properties inline. Two consequences fed bug 3:
      - Mid-operator passes (notably `mesh.separate`) snapshotted half-resolved
        transforms into `ct_field_unk28/2C/30` and `ct_keyframe_holds`, which
        re-projected back as scale/rotation on the wrong object.
      - Newly separated children transiently shared the source's Action, so
        location_keyframes() returned the parent's path for the child.

    This handler now filters via `depsgraph.updates`, drops updates whose
    Action is multi-user (the transient state during Separate), and defers all
    writes to `_flush_pending_sync` via a one-shot timer so writes can't fold
    back into the same depsgraph phase that produced them.
    """
    if _flush_in_progress:
        return

    enqueued = False
    for upd in depsgraph.updates:
        id_data = upd.id
        if isinstance(id_data, bpy.types.Object):
            obj = id_data.original
            if not _is_sync_candidate(obj):
                continue
            if not (upd.is_updated_transform or upd.is_updated_geometry):
                continue
            # Transient multi-user action: a Separate mid-flight makes the new
            # child briefly share the parent's action. Writing through that
            # state corrupts both objects' fields. Skip; the operator's final
            # depsgraph pass will re-enqueue with the action settled.
            if obj.ct.kind in PLATFORM_KINDS:
                ad = obj.animation_data
                if ad and ad.action is not None and ad.action.users > 1:
                    continue
            _pending_sync.add(obj.name)
            enqueued = True
        elif isinstance(id_data, bpy.types.Action):
            if not _action_uses_location(id_data):
                continue
            action_orig = id_data.original
            for obj in bpy.data.objects:
                if not _is_sync_candidate(obj):
                    continue
                ad = obj.animation_data
                if ad and ad.action == action_orig:
                    _pending_sync.add(obj.name)
                    enqueued = True

    if enqueued:
        _arm_flush()


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
    if bpy.app.timers.is_registered(_flush_pending_sync):
        bpy.app.timers.unregister(_flush_pending_sync)
    _pending_sync.clear()
    del bpy.types.Scene.ct
    del bpy.types.Object.ct
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
