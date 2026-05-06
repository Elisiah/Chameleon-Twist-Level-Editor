"""Sidebar panels: Scene-level config + per-object CT properties."""

from pathlib import Path

import bpy

from .properties import CT_KINDS


class CT_PT_scene(bpy.types.Panel):
    bl_label = "CT Mod Scene"
    bl_idname = "CT_PT_scene"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CT"

    def draw(self, context):
        layout = self.layout
        ct = context.scene.ct
        layout.prop(ct, "land")
        layout.operator("ct.set_repo_root", text="Set Repo Root...", icon="FILE_FOLDER")
        if ct.repo_root:
            layout.label(text=ct.repo_root, icon="FILE_FOLDER")

        layout.separator()
        try:
            import fast64  # noqa: F401
            layout.label(text="fast64 detected, F3D materials on import", icon="CHECKMARK")
            layout.prop(ct, "f3d_preset", icon="MATERIAL")
            layout.operator("ct.convert_to_f3d", text="Convert Scene Materials to F3D", icon="MATERIAL")
        except ImportError:
            layout.label(text="fast64 not found, Principled BSDF fallback", icon="INFO")

        layout.separator()
        row = layout.row(align=True)
        row.operator("ct.import_room", text="Import Room...", icon="IMPORT")
        row.operator("ct.export_room", text="Export Room...", icon="FILE_TICK")
        # Auto-detect available room variants (e.g. ext_ for JungleLand).
        if ct.repo_root and ct.land:
            land_c = Path(bpy.path.abspath(ct.repo_root)) / "src" / "levelGroup" / f"{ct.land}.c"
            if land_c.exists():
                try:
                    from . import room_import
                    variants = room_import.list_room_variants(land_c, ct.land)
                    if len(variants) > 1:
                        layout.label(
                            text="Room sets: " + ", ".join(
                                f'"{v}"' if v else '"" (interior)' for v in variants),
                            icon="INFO")
                except Exception:
                    pass

        layout.separator()
        layout.label(text="Add Level Object:")
        col = layout.column(align=True)
        for key, name, _desc in CT_KINDS:
            col.operator("ct.spawn_kind", text=f"+ {name}").kind = key

        layout.separator()
        layout.label(text="Add Actor / Item:")
        row = layout.row(align=True)
        row.operator("ct.spawn_actor", text="+ Actor", icon="ARMATURE_DATA")
        row.operator("ct.spawn_item",  text="+ Item",  icon="DECORATE_ANIMATE")

        layout.separator()
        layout.operator("ct.export_full_mod",
                        text=f"Export Full Mod  ({ct.land or 'set Land'})",
                        icon="EXPORT")
        layout.label(text="imports + edits + new objects -> build/mod/", icon="INFO")


class CT_PT_object(bpy.types.Panel):
    bl_label = "CT Object"
    bl_idname = "CT_PT_object"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CT"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        ct = obj.ct

        array_kind = obj.get("ct_array_kind", "")

        # Custom collision mesh authored by the user
        if obj.type == "MESH" and not array_kind:
            layout.prop(ct, "is_collision")
            if ct.is_collision:
                layout.prop(ct, "model_name")
                if ct.model_name:
                    sym = ct.model_name
                    layout.label(
                        text=f"Collision -> manifests/{context.scene.ct.land or '<Land>'}/{sym}/{sym}.collision.c",
                        icon="CHECKMARK")
                    box = layout.box()
                    box.label(text="fast64: set export symbol to:", icon="INFO")
                    box.label(text=f"  Gfx array = {sym}_Gfx")
                    box.label(text=f"  Vtx array = {sym}_Vtx")
                    box.label(text=f"  Export .c -> manifests/{context.scene.ct.land or '<Land>'}/{sym}/")
                else:
                    layout.label(text="Set Model Symbol to emit collision.c", icon="ERROR")
            return

        # ID / type dropdown
        if array_kind == "objects":
            layout.prop(ct, "model_enum")
            layout.prop(ct, "model_id_override")
            override = (ct.model_id_override or "").strip()
            if override and not override.startswith("_") and not override.endswith("_MODEL") and not override.isdigit():
                if obj.type == "MESH":
                    box = layout.box()
                    box.label(text="Collision: exported via Export Full Mod", icon="CHECKMARK")
                    box.label(text=f"Gfx symbol: {override}_Gfx", icon="INFO")
                    box.operator("ct.export_gfx", text=f"Export Gfx -> manifests/{context.scene.ct.land or '<Land>'}/{override}/", icon="EXPORT")
                else:
                    layout.label(text="No mesh, collision won't export", icon="ERROR")
        elif array_kind == "actors":
            layout.prop(ct, "actor_id_enum")
        elif array_kind == "sprites":
            layout.prop(ct, "sprite_index_enum")
        elif array_kind == "collectables":
            layout.prop(ct, "collectable_id_enum")

        # Room ID is editable for every object : imported or newly spawned.
        row = layout.row()
        row.prop(ct, "room_id")
        land_tag = obj.get("ct_land", context.scene.ct.land or "")
        if land_tag:
            row.label(text=land_tag, icon="WORLD")

        if array_kind:
            layout.label(text=array_kind, icon="INFO")
        else:
            # New level-geometry objects
            layout.prop(ct, "kind")
            layout.prop(ct, "model_name")
            if ct.kind == "exit_trigger":
                box = layout.box()
                box.prop(ct, "exit_direction")
                box.prop(ct, "exit_target_arg")
            elif ct.kind == "pole_grabbable":
                box = layout.box()
                box.prop(ct, "pole_auto_height")
                row = box.row()
                row.prop(ct, "grab_line")
                row.enabled = not ct.pole_auto_height


CLASSES = (CT_PT_scene, CT_PT_object)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)