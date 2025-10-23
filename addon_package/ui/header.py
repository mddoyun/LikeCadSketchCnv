"""3D View header integration for LikeCadSketch."""

import bpy


def draw_header_button(self, context):
    layout = self.layout
    layout.separator_spacer()

    is_mesh_context = context.mode in {"OBJECT", "EDIT_MESH"}
    if context.mode == "OBJECT":
        obj = context.object
        is_mesh_context = obj is None or obj.type == "MESH"
    row = layout.row(align=True)
    row.enabled = is_mesh_context
    row.operator("view3d.cad_line", text="Line", icon="MESH_DATA")


def register():
    bpy.types.VIEW3D_HT_header.append(draw_header_button)


def unregister():
    bpy.types.VIEW3D_HT_header.remove(draw_header_button)
