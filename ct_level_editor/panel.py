"""Sidebar panels: Scene-level config + per-object CT properties."""

from pathlib import Path
import bpy
from . import kinds, properties


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
        for k in kinds.KIND_REGISTRY:
            col.operator("ct.spawn_kind", text=f"+ {k.label}").kind = k.id

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


class CT_OT_set_kind_field(bpy.types.Operator):
    bl_idname = "ct.set_kind_field"
    bl_label = ""
    bl_options = {"INTERNAL"}

    object_name: bpy.props.StringProperty()
    field_name: bpy.props.StringProperty()
    value: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj:
            obj[f"ct_field_{self.field_name}"] = self.value
        return {"FINISHED"}


class CT_PT_object(bpy.types.Panel):
    bl_label = "CT Object"
    bl_idname = "CT_PT_object"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CT"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def _draw_kind_fields(self, layout, obj, kind_def, skip=None):
        """Draw the per-kind field controls in the Object panel."""
        skip = skip or set()
        for f in kind_def.fields:
            if f.c_name in skip:
                continue
            prop_name = f"ct_field_{f.c_name}"
            present = prop_name in obj

            if f.field_type == "enum":
                row = layout.row(align=True)
                row.label(text=f.label)
                current = obj[prop_name] if present else f.default
                for val, label, _desc in f.enum_items:
                    op = row.operator("ct.set_kind_field", text=label,
                                      depress=(str(current) == val))
                    op.object_name = obj.name
                    op.field_name = f.c_name
                    op.value = val
            elif f.field_type in ("int", "float"):
                row = layout.row()
                if present:
                    row.prop(obj, f'["{prop_name}"]', text=f.label)
                else:
                    op = row.operator("ct.set_kind_field", text=f"{f.label}: {f.default} (init)")
                    op.object_name = obj.name
                    op.field_name = f.c_name
                    op.value = str(f.default)
            else:
                row = layout.row()
                row.label(text=f"{f.label}: {obj[prop_name] if present else f.default}")

    def _draw_linear_platform(self, layout, obj):
        """Draw the UI for a linear 2-point moving platform"""
        ct = obj.ct
        box = layout.box()
        box.label(text="Linear 2-Point Platform", icon="OBJECT_DATA")

        keys = properties.location_keyframes(obj)
        info = box.column(align=True)
        if len(keys) >= 2:
            f0, _ = keys[0]
            f1, p1 = keys[1]
            travel = max(1, int(round(f1 - f0)))
            target = properties._bl_to_ct(p1)
            info.label(text=f"Auto-synced from animation:", icon="ANIM_DATA")
            info.label(text=f"  Target  ({target[0]:.1f}, {target[1]:.1f}, {target[2]:.1f})")
            info.label(text=f"  Travel  {travel}f  =  {travel / properties.GAME_FPS:.2f}s")
        else:
            info.label(text="Add 2 location keyframes to auto-sync", icon="INFO")

        box.separator()
        box.prop(ct, "moving_platform_time2_sec", text="Time to Return (s)")

        manual = box.column(align=True)
        manual.label(text="Manual override:")
        manual.prop(obj, '["ct_field_unk28"]', text="Target X")
        manual.prop(obj, '["ct_field_unk2C"]', text="Target Y")
        manual.prop(obj, '["ct_field_unk30"]', text="Target Z")
        manual.prop(obj, '["ct_field_noKeyframes"]', text="Travel Frames")
        manual.prop(obj, '["ct_field_unk44"]', text="Hold Frames")
        box.operator("ct.sync_moving_platform", icon="ANIM_DATA")

    def _draw_keyframed_platform(self, layout, obj):
        """Draw the UI for a keyframed path platform"""
        box = layout.box()
        box.label(text="Keyframed Path Platform", icon="DRIVER")

        keys = properties.location_keyframes(obj)
        if not keys:
            box.label(text="No location keyframes yet", icon="INFO")
            box.operator("ct.seed_keyframed_platform", icon="ADD")
            return

        n = len(keys)
        box.label(text=f"{n} waypoints (auto-synced from F-curves):", icon="ANIM_DATA")
        for i, (f, p) in enumerate(keys):
            ct_p = properties._bl_to_ct(p)
            row = box.row(align=True)
            row.label(text=f"WP{i}  f{int(f)}")
            row.label(text=f"({ct_p[0]:.0f}, {ct_p[1]:.0f}, {ct_p[2]:.0f})")
            if i < n - 1:
                travel = max(1, int(round(keys[i + 1][0] - f)))
                row.label(text=f"-> {travel / properties.GAME_FPS:.2f}s")

        box.separator()
        box.prop(obj.ct, "keyframed_return_sec", text="Hold Before Return (s)")
        box.operator("ct.seed_keyframed_platform", text="Add Default Path", icon="ADD")

    def _draw_export_gfx_button(self, layout, obj, land):
        """Offer a direct fast64 Gfx export button for meshes that have a valid Model ID Override."""
        ct = obj.ct
        override = (ct.model_id_override or "").strip()
        if not override:
            override = (ct.model_name or "").strip()
        if not override or override.startswith("_") or override.endswith("_MODEL") or override.isdigit():
            return
        if obj.type != "MESH":
            layout.label(text="No mesh, collision won't export", icon="ERROR")
            return
        box = layout.box()
        box.label(text="Collision: exported via Export Full Mod", icon="CHECKMARK")
        box.label(text=f"Gfx symbol: {override}_Gfx", icon="INFO")
        box.operator("ct.export_gfx", text=f"Export Gfx -> manifests/{land or '<Land>'}/{override}/", icon="EXPORT")

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        ct = obj.ct
        land = context.scene.ct.land or ""
        array_kind = obj.get("ct_array_kind", "")

        if obj.type == "MESH" and not array_kind:
            layout.prop(ct, "is_collision")
            if ct.is_collision:
                layout.prop(ct, "model_name")
                if ct.model_name:
                    sym = ct.model_name
                    box = layout.box()
                    box.label(text=f"-> manifests/{land or '<Land>'}/{sym}/", icon="CHECKMARK")
                    box.label(text=f"Gfx: {sym}_Gfx   Vtx: {sym}_Vtx", icon="INFO")
                else:
                    layout.label(text="Set Model Symbol to emit collision.c", icon="ERROR")

        if array_kind == "objects":
            layout.prop(ct, "model_enum")
            layout.prop(ct, "model_id_override")
        elif array_kind == "actors":
            layout.prop(ct, "actor_id_enum")
        elif array_kind == "sprites":
            layout.prop(ct, "sprite_index_enum")
        elif array_kind == "collectables":
            layout.prop(ct, "collectable_id_enum")

        row = layout.row()
        row.prop(ct, "room_id")
        land_tag = obj.get("ct_land", land)
        if land_tag:
            row.label(text=land_tag, icon="WORLD")

        layout.prop(ct, "kind")
        layout.prop(ct, "model_name")

        if ct.kind == "pole_grabbable":
            layout.prop(ct, "model_enum")
            layout.prop(ct, "pole_auto_height")

        if ct.kind == "moving_platform_linear":
            self._draw_linear_platform(layout, obj)
            kind_def = kinds.KIND_REGISTRY_BY_ID["moving_platform_linear"]
            self._draw_kind_fields(layout, obj, kind_def,
                                    skip={"unk28", "unk2C", "unk30", "noKeyframes", "unk44"})
        elif ct.kind == "platform_keyframed":
            self._draw_keyframed_platform(layout, obj)
        else:
            kind_def = kinds.KIND_REGISTRY_BY_ID.get(ct.kind)
            if kind_def:
                self._draw_kind_fields(layout, obj, kind_def)

        self._draw_export_gfx_button(layout, obj, land)


CLASSES = (CT_PT_scene, CT_PT_object, CT_OT_set_kind_field)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
