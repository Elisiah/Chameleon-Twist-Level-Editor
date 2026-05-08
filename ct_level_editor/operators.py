"""Blender operators: trigger manifest + collision export, and room import."""

import re
import bpy
import bmesh
import mathutils
from pathlib import Path

from . import manifest_export, room_import, room_export, visual_import
from .properties import CT_KINDS, get_model_items

# ---------------------------------------------------------------------------
# Material helpers, F3D (fast64) when available, Principled BSDF fallback
# ---------------------------------------------------------------------------

_FMT_RE = re.compile(r'\.(ci4|ci8|rgba16|rgba32|i4|i8|ia4|ia8|ia16)\.', re.IGNORECASE)


def _tex_format_from_name(name: str) -> str:
    """Derive F3D tex_format from filename convention, e.g. lavaWall.ci8.png -> CI8."""
    m = _FMT_RE.search(name)
    return m.group(1).upper() if m else "RGBA16"


def _image_has_transparency(img) -> bool:
    """Return True if the image contains any pixel with alpha < 1.

    Used to auto-select opaque vs cutout preset, ropes, sprites, and
    edge-faded surfaces have transparent pixels; solid ground/lava don't.
    """
    if img is None or img.channels < 4:
        return False
    try:
        import numpy as np
        arr = np.empty(len(img.pixels), dtype=np.float32)
        img.pixels.foreach_get(arr)
        return bool((arr[3::4] < 0.99).any())
    except Exception:
        pixels = list(img.pixels)
        return any(pixels[i] < 0.99 for i in range(3, len(pixels), 4))


def _preset_for(img, f3d_preset: str) -> str:
    """Choose the fast64 preset filename for this texture.

    If the user has set a preset override, always use it.
    Otherwise: opaque image -> sm64_shaded_texture,
               image with alpha -> sm64_shaded_texture_cutout.
    """
    if f3d_preset.strip():
        return f3d_preset.strip()
    return "sm64_shaded_texture_cutout" if _image_has_transparency(img) else "sm64_shaded_texture"


def _make_material(img, tex_name: str, f3d_preset: str) -> bpy.types.Material:
    """Return an F3D material if fast64 is installed, else a Principled BSDF fallback.

    Only reuses an existing material by name if it is already an F3D material,
    stale Principled BSDF materials left from earlier imports are ignored.
    """
    # Reuse only if it's already a proper F3D material.
    existing = bpy.data.materials.get(tex_name)
    if existing is not None and getattr(existing, "is_f3d", False):
        return existing
    # Evict a stale BSDF material so Blender won't rename our new F3D one to .001
    if existing is not None and not getattr(existing, "is_f3d", False) and existing.users == 0:
        bpy.data.materials.remove(existing)
        existing = None

    # --- fast64 path ---
    try:
        from fast64.fast64_internal.f3d.f3d_material import createF3DMat
        mat = createF3DMat(None, preset=_preset_for(img, f3d_preset))
        mat.name = tex_name
        f3d = mat.f3d_mat
        # Use temp_override so update_tex_values_and_formats can find the material
        # via get_material_from_context, without this the node tree never refreshes.
        with bpy.context.temp_override(material=mat):
            f3d.tex0.tex = img
            f3d.tex0.tex_set = True
            f3d.tex0.tex_format = _tex_format_from_name(tex_name)
        return mat
    except ImportError:
        pass  # fast64 not installed
    except Exception as e:
        print(f"[CT] fast64 material creation failed for {tex_name!r}: {e}")

    # --- Principled BSDF fallback (reuse existing BSDF or create fresh) ---
    if existing is not None:
        return existing
    mat = bpy.data.materials.new(name=tex_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    tex_node = nodes.new('ShaderNodeTexImage')
    tex_node.image = img
    tex_node.interpolation = "Closest"
    links.new(bsdf.inputs['Base Color'], tex_node.outputs['Color'])
    links.new(bsdf.inputs['Alpha'], tex_node.outputs['Alpha'])
    mat.blend_method = 'CLIP'
    return mat


def _attach_f3d_color_attributes(obj: bpy.types.Object) -> None:
    """Add vertex colour attributes that F3D materials expect (no-op if fast64 absent)."""
    try:
        from fast64.fast64_internal.f3d.f3d_material import addColorAttributesToModel
        addColorAttributesToModel(obj)
    except (ImportError, Exception):
        pass


def _make_mesh_from_gfx(name: str, gfx_data: dict, f3d_preset: str = "") -> bpy.types.Mesh:
    """Build a Blender Mesh from visual_import.parse_gfx output.

    Creates F3D materials (fast64) when available, Principled BSDF otherwise.
    UVs and per-face material indices are assigned via bmesh.
    """
    me = bpy.data.meshes.new(name)
    me.from_pydata(gfx_data['verts'], [], gfx_data['faces'])

    mat_images = []
    for tex_path in gfx_data['tex_paths']:
        img = bpy.data.images.get(tex_path.name)
        if img is None:
            try:
                img = bpy.data.images.load(str(tex_path))
            except Exception:
                img = None
        mat = _make_material(img, tex_path.name, f3d_preset)
        me.materials.append(mat)
        mat_images.append(img)

    if not mat_images:
        me.update()
        return me

    bm = bmesh.new()
    bm.from_mesh(me)
    uv_layer = bm.loops.layers.uv.new("UVMap")
    bm.faces.ensure_lookup_table()

    for fi, face in enumerate(bm.faces):
        ti = gfx_data['face_tex'][fi] if fi < len(gfx_data['face_tex']) else None
        if ti is None:
            continue
        face.material_index = ti
        vtx_indices = gfx_data['faces'][fi]
        img = mat_images[ti] if ti < len(mat_images) else None
        tw = img.size[0] if img and img.size[0] > 0 else 64
        th = img.size[1] if img and img.size[1] > 0 else 64
        for li, loop in enumerate(face.loops):
            vi = vtx_indices[li]
            u_raw, v_raw = gfx_data['uvs'][vi]
            tx = (u_raw / visual_import._UV_DENOM) / (tw / 64.0)
            ty = (v_raw / -visual_import._UV_DENOM) / (th / 64.0) + 1.0
            loop[uv_layer].uv = (tx, ty)

    bm.to_mesh(me)
    bm.free()
    me.update()
    return me


def _pole_sym_items(self, context):
    """Dynamic items for the pole_sym EnumProperty on CT_OT_spawn_kind."""
    items = [("auto", "Auto-detect", "Use the first Pole model found in stageModels")]
    if not context:
        return items
    try:
        scene = context.scene
        repo_root = bpy.path.abspath(scene.ct.repo_root or "")
        land = scene.ct.land or ""
        if not repo_root or not land:
            return items
        land_c = Path(repo_root) / "src" / "levelGroup" / f"{land}.c"
        if not land_c.exists():
            return items
        source = land_c.read_text()
        stage_models = room_import.parse_stage_models(source, land)
        for col_sym in stage_models:
            if "pole" not in col_sym.lower():
                continue
            sym = col_sym[:-len("_collision")] if col_sym.endswith("_collision") else col_sym
            items.append((sym, sym, f"Prefab: {sym}"))
    except Exception:
        pass
    return items


class CT_OT_spawn_kind(bpy.types.Operator):
    bl_idname = "ct.spawn_kind"
    bl_label = "Spawn CT Object"
    bl_description = "Add an Empty at the 3D cursor pre-configured with this CT kind"
    bl_options = {"REGISTER", "UNDO"}

    kind: bpy.props.EnumProperty(items=CT_KINDS)
    room_id: bpy.props.IntProperty(name="Room ID", default=0, min=0)
    room_variant: bpy.props.StringProperty(name="Room Variant", default="",
        description="Optional prefix for exterior rooms, e.g. 'ext_'")
    pole_sym: bpy.props.EnumProperty(
        name="Pole Model",
        items=_pole_sym_items,
        description="Which pole model to use as the visual prefab and default model_enum")

    def invoke(self, context, event):
        if self.kind == "pole_grabbable":
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        label = dict((k, n) for k, n, _ in CT_KINDS).get(self.kind, self.kind)
        display = "SINGLE_ARROW" if self.kind == "exit_trigger" else "PLAIN_AXES"
        bpy.ops.object.empty_add(type=display, location=context.scene.cursor.location)
        obj = context.active_object
        obj.name = f"CT_{label.replace(' ', '')}"
        obj.ct.kind = self.kind
        obj.ct.room_id = self.room_id
        # Stamp land + room so the exporter can find this object.
        obj["ct_land"] = context.scene.ct.land
        obj["ct_room_id"] = self.room_id
        obj["ct_room_variant"] = self.room_variant

        if self.kind == "pole_grabbable":
            self._attach_pole_prefab(context, obj)

        return {"FINISHED"}

    def _attach_pole_prefab(self, context, parent_empty):
        scene = context.scene
        repo_root = bpy.path.abspath(scene.ct.repo_root or "")
        land = scene.ct.land or ""
        if not repo_root or not land:
            return
        land_c = Path(repo_root) / "src" / "levelGroup" / f"{land}.c"
        if not land_c.exists():
            return
        try:
            source = land_c.read_text()
            stage_models = room_import.parse_stage_models(source, land)

            # Resolve the chosen pole symbol from the operator property.
            chosen = getattr(self, "pole_sym", "auto")
            if chosen and chosen != "auto":
                # User picked a specific symbol : find its collision entry.
                col_sym = next(
                    (s for s in stage_models
                     if s.endswith("_collision") and s[:-len("_collision")] == chosen),
                    None,
                )
            else:
                #col_sym = next((s for s in stage_models if "Pole" in s), None)
                col_sym = next((s for s in stage_models if s == "Global_pole_collision"), None)
                if col_sym is None:
                    col_sym = next((s for s in stage_models if "pole" in s.lower()), None)

            if col_sym is None:
                return

            # Set the model_enum on the empty to match chosen pole.
            display_sym = col_sym[:-len("_collision")] if col_sym.endswith("_collision") else col_sym
            # Find which enum name in the land's model enum maps to this stageModels index.
            enum_map = room_import.parse_model_enum(source)
            sym_index = stage_models.index(col_sym) if col_sym in stage_models else -1
            if sym_index >= 0:
                # Find enum name with this value.
                for name, val in enum_map.items():
                    if val == sym_index:
                        try:
                            parent_empty.ct.model_enum = name
                        except Exception:
                            pass
                        break

            gfx_file = visual_import.find_gfx_file(Path(repo_root), col_sym)
            if gfx_file is None:
                return
            gfx_data = visual_import.parse_gfx(gfx_file, gfx_file.parent, Path(repo_root))
            if not gfx_data.get("faces"):
                return
            f3d_preset = getattr(scene.ct, "f3d_preset", "")
            me = _make_mesh_from_gfx(f"{display_sym}_preview", gfx_data, f3d_preset)
            prefab = bpy.data.objects.new(f"{display_sym}_preview", me)
            context.collection.objects.link(prefab)
            _attach_f3d_color_attributes(prefab)
            prefab.parent = parent_empty
            prefab.hide_select = True
            prefab.display_type = "SOLID"
        except Exception as e:
            self.report({"WARNING"}, f"Pole prefab load failed: {e}")


class CT_OT_set_repo_root(bpy.types.Operator):
    bl_idname = "ct.set_repo_root"
    bl_label = "Set Repo Root"
    bl_description = "Browse for the CT decomp repo root directory"

    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        context.scene.ct.repo_root = self.directory
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class CT_OT_export_mod(bpy.types.Operator):
    bl_idname = "ct.export_mod"
    bl_label = "Export CT Mod"
    bl_description = "Walk the scene and write the manifest + collision files into <repo>/tools/LevelEditor/manifests/<Land>/"

    def execute(self, context):
        scene = context.scene
        repo_root = bpy.path.abspath(scene.ct.repo_root or "")
        if not repo_root or not Path(repo_root).exists():
            self.report({"ERROR"}, "Set Scene -> CT -> Repo Root to your decomp checkout first")
            return {"CANCELLED"}
        if not scene.ct.land:
            self.report({"ERROR"}, "Set Scene -> CT -> Land (e.g. 'AntLand')")
            return {"CANCELLED"}

        manifest_dir = Path(repo_root) / "tools" / "LevelEditor" / "manifests" / scene.ct.land
        try:
            summary = manifest_export.export_scene(scene, manifest_dir)
        except Exception as e:
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}

        self.report({"INFO"},
                    f"Wrote manifest with {summary['appended_model_count']} models, "
                    f"{summary['object_count']} object placements, "
                    f"{len(summary['collision_assets'])} collision assets -> {summary['manifest_path']}")
        return {"FINISHED"}


def _is_null_entry(entry: dict, array_kind: str) -> bool:
    """Return True for null-terminator entries that should not be imported as empties.

    Null terminators are always re-added by emit_room_arrays_for_mod; importing
    them causes a double-sentinel when the array is exported again.
    """
    pos = entry.get("position")
    fields = entry.get("fields", [])
    if array_kind == "objects":
        # Null: pos AND scale are all zero.
        if pos and all(abs(c) < 0.001 for c in pos):
            if len(fields) > room_import.ROOM_OBJECT_SCALE_FIELD:
                scale = room_import.parse_vec3(fields[room_import.ROOM_OBJECT_SCALE_FIELD])
                if scale and all(abs(c) < 0.001 for c in scale):
                    return True
    elif array_kind in ("actors", "collectables"):
        # Null: id field is 0 or ACTOR_NULL and position is (0,0,0).
        if fields and fields[0].strip() in ("0", "ACTOR_NULL"):
            if pos and all(abs(c) < 0.001 for c in pos):
                return True
    elif array_kind == "sprites":
        # Null: first field is -1 (null sprite type).
        if fields and fields[0].strip() == "-1":
            return True
    return False


class CT_OT_import_room(bpy.types.Operator):
    bl_idname = "ct.import_room"
    bl_label = "Import Room"
    bl_description = "Load a vanilla room's placements from the decomp repo into the scene as Empties"
    bl_options = {"REGISTER", "UNDO"}

    room_id: bpy.props.IntProperty(name="Room ID", default=0, min=0)
    room_variant: bpy.props.StringProperty(
        name="Room Set Prefix",
        default="",
        description="Optional prefix for exterior/variant rooms, e.g. 'ext_' for JungleLand_ext_room1_objects"
    )

    def execute(self, context):
        scene = context.scene
        repo_root = bpy.path.abspath(scene.ct.repo_root or "")
        if not repo_root or not Path(repo_root).exists():
            self.report({"ERROR"}, "Set Repo Root to your decomp checkout first")
            return {"CANCELLED"}
        land = scene.ct.land
        if not land:
            self.report({"ERROR"}, "Set Land (e.g. 'AntLand') first")
            return {"CANCELLED"}

        land_c = Path(repo_root) / "src" / "levelGroup" / f"{land}.c"
        if not land_c.exists():
            self.report({"ERROR"}, f"Not found: {land_c}")
            return {"CANCELLED"}

        try:
            arrays = room_import.parse_room_arrays(land_c, land, self.room_id, self.room_variant)
        except Exception as e:
            self.report({"ERROR"}, f"Parse failed: {e}")
            return {"CANCELLED"}

        total = sum(len(v) for v in arrays.values())
        if total == 0:
            self.report({"WARNING"}, f"No entries found for {land} room {self.room_id}")
            return {"CANCELLED"}

        source = land_c.read_text()
        enum_map = room_import.parse_model_enum(source)
        stage_models = room_import.parse_stage_models(source, land)
        repo_path = Path(repo_root)
        collection = context.collection
        f3d_preset = getattr(scene.ct, "f3d_preset", "")

        # Build one mesh data-block per unique model symbol, shared across instances.
        # GFX geometry (textured) is preferred; collision-only is the fallback.
        col_meshes = room_import.load_collision_meshes(
            source, land, repo_path, arrays.get("objects", []), enum_map
        )
        mesh_by_symbol: dict[str, bpy.types.Mesh] = {}
        for mesh_data in col_meshes:
            sym = mesh_data["symbol"]
            model_name = mesh_data["name"]
            gfx_file = visual_import.find_gfx_file(repo_path, sym)
            me = None
            if gfx_file is not None:
                try:
                    gfx_data = visual_import.parse_gfx(gfx_file, gfx_file.parent, repo_path)
                    if gfx_data['faces']:
                        me = _make_mesh_from_gfx(model_name, gfx_data, f3d_preset)
                except Exception as e:
                    self.report({"WARNING"}, f"GFX parse failed for {sym}: {e}")
            if me is None:
                me = bpy.data.meshes.new(model_name)
                me.from_pydata(mesh_data["verts"], [], mesh_data["tris"])
                me.update()
            mesh_by_symbol[sym] = me

        spawned_objects = 0

        # --- RoomObject entries -> named mesh objects (or empties if model unresolvable) ---
        for i, entry in enumerate(arrays.get("objects", [])):
            pos = entry.get("position")
            if pos is None or _is_null_entry(entry, "objects"):
                continue

            fields = entry.get("fields", [])
            bpos = room_import.ct_position_to_blender(pos)

            # Resolve model symbol for naming and mesh lookup.
            sym = None
            obj_name = f"{land}_r{self.room_id}_obj{i}"
            if len(fields) > room_import.ROOM_OBJECT_MODEL_FIELD:
                model_id = room_import.resolve_model_id(
                    fields[room_import.ROOM_OBJECT_MODEL_FIELD], enum_map
                )
                if model_id is not None and 0 < model_id < len(stage_models):
                    sym = stage_models[model_id]
                    if sym:
                        obj_name = sym[:-len("_collision")] if sym.endswith("_collision") else sym

            me = mesh_by_symbol.get(sym) if sym else None
            obj = bpy.data.objects.new(obj_name, me)
            collection.objects.link(obj)
            obj.location = bpos
            if me is not None:
                _attach_f3d_color_attributes(obj)
            else:
                obj.empty_display_type = "PLAIN_AXES"
                obj.empty_display_size = 30

            obj["ct_raw_entry"] = entry["raw"]
            obj["ct_array_kind"] = "objects"
            obj["ct_room_id"] = self.room_id
            obj["ct_room_variant"] = self.room_variant
            obj["ct_land"] = land
            obj["ct_original_pos"] = list(pos)
            obj["ct_original_model_sym"] = obj_name  # CamelCase sym (no _collision)
            obj.ct.room_id = self.room_id

            if len(fields) > room_import.ROOM_OBJECT_SCALE_FIELD:
                ct_scale = room_import.parse_vec3(fields[room_import.ROOM_OBJECT_SCALE_FIELD])
                if ct_scale is not None:
                    obj.scale = room_import.ct_scale_to_blender(ct_scale)
                    obj["ct_original_scale"] = list(ct_scale)

            axis = 0
            angle_rad = 0.0
            if len(fields) > 3:
                try:
                    axis = int(fields[2].strip())       # unk18
                    angle_rad = room_import.parse_rotation_rad(fields[3])  # damages
                except (ValueError, IndexError):
                    axis = 0
                    angle_rad = 0.0

            euler = mathutils.Euler((0.0, 0.0, 0.0))
            if axis == 1:
                euler.x = angle_rad
            elif axis == 2:
                euler.z = angle_rad
            elif axis == 3:
                euler.y = -angle_rad
            obj.rotation_euler = euler
            obj["ct_original_axis"] = axis
            obj["ct_original_angle_rad"] = angle_rad

            if len(fields) > room_import.ROOM_OBJECT_MODEL_FIELD:
                raw_id = fields[room_import.ROOM_OBJECT_MODEL_FIELD].strip()
                if raw_id:
                    try:
                        obj.ct.model_enum = raw_id
                    except Exception:
                        pass

            spawned_objects += 1

        # --- Actor / Collectable / Sprite entries -> named empties ---
        _EMPTY_KINDS = {
            "actors":       ("SPHERE",     "actor_id_enum",       0),
            "collectables": ("CIRCLE",     "collectable_id_enum", 0),
            "sprites":      ("PLAIN_AXES", "sprite_index_enum",   1),
        }
        _LABEL = {"actors": "Actor", "collectables": "Item", "sprites": "Sprite"}

        spawned_empties = 0
        for array_kind, (disp_type, enum_prop, id_field) in _EMPTY_KINDS.items():
            for i, entry in enumerate(arrays.get(array_kind, [])):
                pos = entry.get("position")
                if pos is None or _is_null_entry(entry, array_kind):
                    continue

                fields = entry.get("fields", [])
                enum_val = fields[id_field].strip() if len(fields) > id_field else ""
                obj_name = enum_val or f"{land}_r{self.room_id}_{_LABEL[array_kind]}{i}"

                bpos = room_import.ct_position_to_blender(pos)
                obj = bpy.data.objects.new(obj_name, None)
                obj.empty_display_type = disp_type
                obj.empty_display_size = 50
                collection.objects.link(obj)
                obj.location = bpos

                obj["ct_raw_entry"] = entry["raw"]
                obj["ct_array_kind"] = array_kind
                obj["ct_room_id"] = self.room_id
                obj["ct_room_variant"] = self.room_variant
                obj["ct_land"] = land
                obj["ct_original_pos"] = list(pos)
                obj.ct.room_id = self.room_id

                if enum_val:
                    try:
                        setattr(obj.ct, enum_prop, enum_val)
                    except Exception:
                        pass

                spawned_empties += 1

        self.report(
            {"INFO"},
            f"Imported {spawned_objects} mesh placements + {spawned_empties} actor/item/sprite empties for {land} room {self.room_id}"
        )
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class CT_OT_export_room(bpy.types.Operator):
    bl_idname = "ct.export_room"
    bl_label = "Export Room (Round-trip)"
    bl_description = (
        "Re-emit the imported room's C arrays from the current scene. "
        "Writes to tools/LevelEditor/roundtrip/<Land>_room<N>.c for diffing."
    )

    room_id: bpy.props.IntProperty(name="Room ID", default=0, min=0)

    def execute(self, context):
        scene = context.scene
        repo_root = bpy.path.abspath(scene.ct.repo_root or "")
        if not repo_root or not Path(repo_root).exists():
            self.report({"ERROR"}, "Set Repo Root first")
            return {"CANCELLED"}
        land = scene.ct.land
        if not land:
            self.report({"ERROR"}, "Set Land first")
            return {"CANCELLED"}

        out_path = (
            Path(repo_root)
            / "tools" / "LevelEditor" / "roundtrip"
            / f"{land}_room{self.room_id}.c"
        )
        try:
            count = room_export.write_room_export(land, self.room_id, out_path)
        except Exception as e:
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}

        if count == 0:
            self.report({"WARNING"}, f"No imported entries found for {land} room {self.room_id}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Wrote {count} entries -> {out_path}")
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class CT_OT_spawn_actor(bpy.types.Operator):
    bl_idname = "ct.spawn_actor"
    bl_label = "Spawn Actor"
    bl_description = "Add a RoomActor Empty at the 3D cursor (choose id via CT Object panel)"
    bl_options = {"REGISTER", "UNDO"}

    room_id: bpy.props.IntProperty(name="Room ID", default=0, min=0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        bpy.ops.object.empty_add(type="SPHERE", location=context.scene.cursor.location)
        obj = context.active_object
        obj.name = f"CT_Actor_r{self.room_id}"
        obj["ct_array_kind"] = "actors"
        obj["ct_room_id"] = self.room_id
        obj["ct_land"] = context.scene.ct.land
        # No ct_raw_entry -> room_export generates a template from _ACTOR_TEMPLATE.
        return {"FINISHED"}


class CT_OT_spawn_item(bpy.types.Operator):
    bl_idname = "ct.spawn_item"
    bl_label = "Spawn Collectable"
    bl_description = "Add a Collectable Empty at the 3D cursor (choose id via CT Object panel)"
    bl_options = {"REGISTER", "UNDO"}

    room_id: bpy.props.IntProperty(name="Room ID", default=0, min=0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        bpy.ops.object.empty_add(type="CIRCLE", location=context.scene.cursor.location)
        obj = context.active_object
        obj.name = f"CT_Item_r{self.room_id}"
        obj["ct_array_kind"] = "collectables"
        obj["ct_room_id"] = self.room_id
        obj["ct_land"] = context.scene.ct.land
        # No ct_raw_entry -> room_export generates a template from _COLLECTABLE_TEMPLATE.
        return {"FINISHED"}


class CT_OT_export_full_mod(bpy.types.Operator):
    bl_idname = "ct.export_full_mod"
    bl_label = "Export Full Mod"
    bl_description = (
        "Closed-loop export: collects every edited/added room from the scene, "
        "writes a complete manifest (raw_replace + stageModels.append), "
        "then runs codegen to produce build/mod/levelGroup/<Land>.c + <Land>_mod.inc.c"
    )

    def execute(self, context):
        import importlib.util, json as _json
        scene = context.scene
        repo_root = bpy.path.abspath(scene.ct.repo_root or "")
        if not repo_root or not Path(repo_root).exists():
            self.report({"ERROR"}, "Set Repo Root first")
            return {"CANCELLED"}
        land = scene.ct.land
        if not land:
            self.report({"ERROR"}, "Set Land first")
            return {"CANCELLED"}

        manifest_dir = Path(repo_root) / "tools" / "LevelEditor" / "manifests" / land
        manifest_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 1: base manifest from manifest_export (stageModels.append for
        # any custom collision-mesh objects the user has authored). ─────────────
        try:
            manifest_export.export_scene(scene, manifest_dir)
        except Exception as e:
            self.report({"ERROR"}, f"Manifest export failed: {e}")
            return {"CANCELLED"}

        manifest_path = manifest_dir / f"{land}_mod.json"
        manifest = _json.loads(manifest_path.read_text())

        # ── Step 2: discover which (room_id) pairs have placement data in the
        # scene and generate raw_replace arrays for each. ──────────────────────
        touched_rooms: set[tuple] = set()
        for obj in bpy.data.objects:
            if obj.get("ct_land") != land:
                continue
            room_id = obj.get("ct_room_id")
            if room_id is None:
                ct = getattr(obj, "ct", None)
                if ct:
                    room_id = ct.room_id
            if room_id is not None:
                variant = obj.get("ct_room_variant", "")
                touched_rooms.add((str(variant), int(room_id)))

        rooms_block = manifest.setdefault("rooms", {})
        total_rooms = 0
        for room_variant, room_id in sorted(touched_rooms):
            try:
                replacements = room_export.emit_room_arrays_for_mod(land, room_id, room_variant)
            except Exception as e:
                self.report({"WARNING"}, f"Room {room_variant}{room_id} export failed: {e}")
                continue
            if not replacements:
                continue
            room_key = f"{room_variant}{room_id}"
            room_block = rooms_block.setdefault(room_key, {})
            room_block["raw_replace"] = replacements
            total_rooms += 1

        manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n")

        # ── Step 3: codegen.prepare_mod ───────────────────────────────────────
        codegen_path = Path(repo_root) / "tools" / "LevelEditor" / "codegen.py"
        if not codegen_path.exists():
            self.report({"ERROR"}, f"codegen.py not found at {codegen_path}")
            return {"CANCELLED"}

        try:
            spec = importlib.util.spec_from_file_location("_ct_codegen", codegen_path)
            cg = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cg)

            build_root = Path(repo_root) / "build"
            cg_report = cg.ManifestReport()
            fresh_manifest = _json.loads(manifest_path.read_text())
            cg.validate_manifest(fresh_manifest, cg_report)
            if cg_report.warnings:
                for w in cg_report.warnings:
                    self.report({"WARNING"}, str(w))
            if not cg_report.ok:
                for e in cg_report.errors:
                    self.report({"ERROR"}, str(e))
                return {"CANCELLED"}

            result = cg.prepare_mod(manifest_path, build_root, cg_report)
            if result is None or not cg_report.ok:
                for e in cg_report.errors:
                    self.report({"ERROR"}, str(e))
                return {"CANCELLED"}

            gated_c, mod_inc = result
            self.report(
                {"INFO"},
                f"Mod build ready : {total_rooms} room(s) exported.\n"
                f"  {gated_c}\n  {mod_inc}",
            )
        except Exception as e:
            self.report({"ERROR"}, f"codegen failed: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


class CT_OT_export_gfx(bpy.types.Operator):
    bl_idname = "ct.export_gfx"
    bl_label = "Export Gfx (fast64)"
    bl_description = (
        "Export the active mesh as a fast64 display list to "
        "manifests/<Land>/<Model ID Override>/, bypasses the file browser "
        "so the path is always correct regardless of OS/WSL quirks"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            import fast64  # noqa: F401
        except ImportError:
            self.report({"ERROR"}, "fast64 not found, install and enable it first")
            return {"CANCELLED"}

        obj = context.active_object
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        override = getattr(obj.ct, "model_id_override", "").strip()
        if not override or override.endswith("_MODEL") or override.isdigit() or override.startswith("_"):
            self.report({"ERROR"},
                        "Set Model ID Override to a new asset name first "
                        "(e.g. JungleLand_intZero_noHole)")
            return {"CANCELLED"}

        repo_root = bpy.path.abspath(context.scene.ct.repo_root or "")
        land = context.scene.ct.land
        if not repo_root or not land:
            self.report({"ERROR"}, "Set Repo Root and Land first")
            return {"CANCELLED"}

        # fast64's exportF3DtoC appends toAlnum(DLName) as a subfolder, so point to
        # the parent (land manifests dir), it will create {land}/{override}/ itself.
        land_manifests_dir = Path(repo_root) / "tools" / "LevelEditor" / "manifests" / land
        land_manifests_dir.mkdir(parents=True, exist_ok=True)
        export_dir = land_manifests_dir / override  # for the success message only

        # Set fast64 scene properties, path and DL name, then call its operator.
        # fast64 applies a 90° X rotation internally (Blender Z-up -> N64 Y-up) and
        # reverses it after, so we don't need to touch the object transform.
        context.scene.DLExportPath = str(land_manifests_dir) + "/"
        context.scene.DLName = override
        context.scene.blenderF3DScale = 1.0
        context.scene.DLExportisStatic = True

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        try:
            result = bpy.ops.object.f3d_export_dl()
        except Exception as e:
            self.report({"ERROR"}, f"fast64 export failed: {e}")
            return {"CANCELLED"}

        if "FINISHED" in result:
            self.report({"INFO"}, f"Gfx exported -> {export_dir}/")
        return result


class CT_OT_convert_to_f3d(bpy.types.Operator):
    bl_idname = "ct.convert_to_f3d"
    bl_label = "Convert Scene Materials to F3D"
    bl_description = (
        "Convert all Principled BSDF materials on CT mesh objects to F3D materials "
        "(requires fast64). Use this if you imported rooms before fast64 was active."
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            from fast64.fast64_internal.f3d.f3d_material import createF3DMat, addColorAttributesToModel
        except ImportError:
            self.report({"ERROR"}, "fast64 not found, install and enable it first")
            return {"CANCELLED"}

        f3d_preset = getattr(context.scene.ct, "f3d_preset", "")
        converted = 0
        errors = []

        for obj in context.scene.objects:
            if obj.type != "MESH" or not obj.get("ct_land"):
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or getattr(mat, "is_f3d", False):
                    continue
                # Extract image from Principled BSDF Base Color if present
                img = None
                if mat.use_nodes and "Principled BSDF" in mat.node_tree.nodes:
                    bc = mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
                    if bc.links and isinstance(bc.links[0].from_node, bpy.types.ShaderNodeTexImage):
                        img = bc.links[0].from_node.image
                try:
                    new_mat = createF3DMat(None, preset=_preset_for(img, f3d_preset))
                    new_mat.name = mat.name + "_f3d"
                    f3d = new_mat.f3d_mat
                    with bpy.context.temp_override(material=new_mat):
                        f3d.tex0.tex = img
                        f3d.tex0.tex_set = True
                        f3d.tex0.tex_format = _tex_format_from_name(mat.name)
                    slot.material = new_mat
                    converted += 1
                except Exception as e:
                    errors.append(f"{mat.name}: {e}")

            if any(getattr(s.material, "is_f3d", False) for s in obj.material_slots if s.material):
                addColorAttributesToModel(obj)

        msg = f"Converted {converted} material(s) to F3D"
        if errors:
            msg += f" ({len(errors)} failed, check console)"
            for err in errors:
                print(f"[CT] F3D convert error: {err}")
        self.report({"INFO"}, msg)
        return {"FINISHED"}


CLASSES = (
    CT_OT_spawn_kind, CT_OT_spawn_actor, CT_OT_spawn_item,
    CT_OT_set_repo_root, CT_OT_import_room, CT_OT_export_room,
    CT_OT_export_mod, CT_OT_export_full_mod, CT_OT_convert_to_f3d, CT_OT_export_gfx,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
