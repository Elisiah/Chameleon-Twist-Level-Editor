"""Chameleon Twist Level Editor : Blender add-on.

Authors mod manifests and exports collision meshes for the CT N64 decomp.
Designed to run alongside fast64 (which handles visual mesh / Gfx export).

Conventions:
- Scene -> Properties -> CT panel: set Land + Repo Root.
- Each Empty tagged with `Object Properties -> CT -> Kind` becomes a manifest entry.
- Mesh objects with `Is Collision Mesh` checked emit a self-contained `.collision.c`
  whose ModelCollision symbol matches `Model Symbol`.
- Run "Export CT Mod" from the View3D > N panel > CT tab.
"""

bl_info = {
    "name": "Chameleon Twist Level Editor",
    "author": "CT decomp modding",
    "version": (0, 1, 0),
    "blender": (4, 1, 0),
    "location": "View3D > Sidebar > CT",
    "description": "Author levels and objects for the Chameleon Twist N64 decomp",
    "category": "Import-Export",
}

from . import properties, operators, panel


def register():
    properties.register()
    operators.register()
    panel.register()


def unregister():
    panel.unregister()
    operators.unregister()
    properties.unregister()
