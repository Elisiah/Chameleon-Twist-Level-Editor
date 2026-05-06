"""PropertyGroups attached to Object and Scene."""

import re
from pathlib import Path

import bpy

CT_KINDS = [
    ("static_mesh",       "Static Mesh",         "Decoration / level geometry (no behavior)"),
    ("moving_platform",   "Moving Platform",     "Animated platform with keyframes"),
    ("pole_grabbable",    "Pole (Grabbable)",    "Tongue-grabbable pole : line geometry"),
    ("exit_trigger",      "Exit Trigger",        "Level exit / room transition"),
    ("fixed_cam_trigger", "Fixed Camera Trigger","Camera anchor pillar"),
    ("tilt_platform",     "Tilt Platform",       "Statically rotated platform"),
]

EXIT_DIRECTIONS = [("N", "North", ""), ("S", "South", ""), ("E", "East", ""), ("W", "West", "")]

_model_items_cache: list = [("_none", "(set Repo Root + Land)", "")]
_actor_items_cache: list = [("ACTOR_NULL", "ACTOR_NULL", "id=0")]
_sprite_items_cache: list = [("SPRITE_BLANK", "SPRITE_BLANK", "id=0")]


def _read_land_source(context) -> str | None:
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
    """Parse a C enum body -> [(identifier, label, description)] with sequential values."""
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


def get_model_items(self, context) -> list:
    global _model_items_cache
    try:
        source = _read_land_source(context)
        if source is None:
            return [("_none", "(set Repo Root + Land)", "")]
        from . import room_import
        enum_map = room_import.parse_model_enum(source)
        items = [(name, name, f"index={val}") for name, val in sorted(enum_map.items(), key=lambda x: x[1])]
        # Always keep _none as first entry so unset properties don't silently
        # default to G_FALLBACK_CUBE_MODEL (index 0) and corrupt raw entries.
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



# PropertyGroups
def _sync_room_id(self, context):
    """Keep the ct_room_id custom property in sync when the user edits Room ID in the panel."""
    if context and context.active_object:
        context.active_object["ct_room_id"] = self.room_id


class CTObjectProps(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(
        name="Kind", items=CT_KINDS, default="static_mesh",
        description="What this object becomes in the manifest")
    room_id: bpy.props.IntProperty(
        name="Room ID", default=0, min=0, update=_sync_room_id,
        description="Which <Land>_roomN arrays this entry joins : editable, updates on export")
    model_name: bpy.props.StringProperty(
        name="Model Symbol", default="",
        description="C symbol of the StageModel (e.g. 'AntLand_modCustomPlatform') : for new objects")
    is_collision: bpy.props.BoolProperty(
        name="Is Collision Mesh", default=False,
        description="If set, this Mesh exports as a CT collision .inc.c")

    # Kind-specific extras
    exit_direction: bpy.props.EnumProperty(
        name="Exit Direction", items=EXIT_DIRECTIONS, default="N")
    exit_target_arg: bpy.props.IntProperty(
        name="Exit Target Arg", default=0)

    # Pole-specific extras
    grab_line: bpy.props.FloatProperty(
        name="Grab Line (Height)", default=100.0,
        description="Height of the grabbable pole in CT units (Z-axis)")
    pole_auto_height: bpy.props.BoolProperty(
        name="Auto Update", default=True,
        description="Automatically update height (grab_line) based on object scale")

    # Dynamic dropdowns for imported entries (spliced back via room_export).
    model_enum: bpy.props.EnumProperty(
        name="Model", items=get_model_items,
        description="StageModel enum name for this RoomObject (updates ct_raw_entry on export)")
    model_id_override: bpy.props.StringProperty(
        name="Model ID Override", default="",
        description="Overrides the Model dropdown: type a vanilla enum name, raw number, or mod-defined symbol (e.g. MY_MOD_PLATFORM). Takes priority on export.")
    actor_id_enum: bpy.props.EnumProperty(
        name="Actor ID", items=get_actor_items,
        description="actorIDs enum name for this RoomActor (updates ct_raw_entry on export)")
    sprite_index_enum: bpy.props.EnumProperty(
        name="Sprite", items=get_sprite_items,
        description="SPRITE enum name for this SpriteActor (updates ct_raw_entry on export)")
    collectable_id_enum: bpy.props.EnumProperty(
        name="Collectable", items=get_actor_items,
        description="actorIDs enum name for this Collectable (CROWN, R_HEART, etc.)")


class CTSceneProps(bpy.types.PropertyGroup):
    land: bpy.props.StringProperty(
        name="Land", default="AntLand",
        description="Target <Land>.c (must exist in src/levelGroup/)")
    repo_root: bpy.props.StringProperty(
        name="Repo Root", subtype='DIR_PATH', default="",
        description="Path to the CT decomp repo (where ./configure lives)")
    f3d_preset: bpy.props.StringProperty(
        name="F3D Preset", default="",
        description="fast64 preset filename for imported GFX meshes. Use the file form: sm64_shaded_texture_cutout, sm64_unlit_texture, or your saved CT preset filename. Leave blank to default to sm64_shaded_texture_cutout. No effect if fast64 is not installed.")


@bpy.app.handlers.persistent
def update_pole_heights(scene):
    """Monitor for scale changes and update grab_line if Auto Update is on."""
    for obj in bpy.data.objects:
        if obj.type == 'EMPTY' and hasattr(obj, "ct"):
            ct = obj.ct
            if ct.kind == 'pole_grabbable' and ct.pole_auto_height:
                # Use the Z dimension of the object to ensure pole is grabbable everywhere
                new_height = round(obj.dimensions.z, 4)
                if new_height < 0.001:
                    continue
                if abs(ct.grab_line - new_height) > 0.0001:
                    ct.grab_line = new_height


CLASSES = (CTObjectProps, CTSceneProps)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.ct = bpy.props.PointerProperty(type=CTObjectProps)
    bpy.types.Scene.ct = bpy.props.PointerProperty(type=CTSceneProps)
    bpy.app.handlers.depsgraph_update_post.append(update_pole_heights)


def unregister():
    if update_pole_heights in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(update_pole_heights)
    del bpy.types.Scene.ct
    del bpy.types.Object.ct
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)