"""Addon entry point for LikeCadSketch Blender add-on."""

import bpy

from .operators.line_tool import VIEW3D_OT_cad_line
from .operators.trim_tool import VIEW3D_OT_cad_trim
from .ui import header as ui_header

bl_info = {
    "name": "Like CAD Sketch",
    "author": "mddoyun",
    "version": (0, 1, 0),
    "blender": (4, 5, 3),
    "location": "View3D > Header",
    "description": "SketchUp-like line drafting tools with CAD-style interaction.",
    "category": "3D View",
}


classes = (
    VIEW3D_OT_cad_line,
    VIEW3D_OT_cad_trim,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    ui_header.register()


def unregister():
    ui_header.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
