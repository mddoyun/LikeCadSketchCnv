"""CAD-style line drawing operator for LikeCadSketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import bpy
import bmesh
from bmesh.types import BMVert
from bpy.types import Context, Event
from bpy_extras import view3d_utils
from mathutils import Matrix, Vector
from mathutils import geometry as geom


AXIS_VECTORS = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}


@dataclass
class ConstraintState:
    """Represents the current axis constraint."""

    axis: Optional[str] = None
    exclude_axis: bool = False

    def label(self) -> str:
        if not self.axis:
            return "Free"
        return f"Shift+{self.axis}" if self.exclude_axis else self.axis


@dataclass
class SnapState:
    """Represents the current snap state."""

    snap_type: Optional[str] = None
    target_world: Optional[Vector] = None
    target_screen: Optional[Vector] = None

    def label(self) -> str:
        if self.snap_type == 'VERTEX':
            return "Vertex"
        if self.snap_type == 'MIDPOINT':
            return "Midpoint"
        return "None"


class VIEW3D_OT_cad_line(bpy.types.Operator):
    """Interactively create straight edges with CAD-style controls."""

    bl_idname = "view3d.cad_line"
    bl_label = "CAD Line"
    bl_description = "Draw edges with CAD-like snapping, axis locks, and numeric input"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    # ----- lifecycle helpers -------------------------------------------------
    def invoke(self, context: Context, event: Event):
        if context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "3D View only")
            return {"CANCELLED"}

        self._reset_state()

        self._ensure_edit_mesh(context)
        if context.mode != "EDIT_MESH":
            self.report({"WARNING"}, "Failed to enter Edit Mode")
            return {"CANCELLED"}

        self._active_obj = context.edit_object
        self._matrix_world = self._active_obj.matrix_world.copy()
        self._matrix_world_inv = self._matrix_world.inverted()
        self._bm = bmesh.from_edit_mesh(self._active_obj.data)
        self._bm.verts.ensure_lookup_table()
        self._bm.edges.ensure_lookup_table()

        self._mouse_region = (event.mouse_region_x, event.mouse_region_y)
        self._update_status_text(context, "Line tool started")

        context.window.cursor_set('CROSSHAIR')
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            self._mouse_region = (event.mouse_region_x, event.mouse_region_y)
            self._preview_world = self._constrained_point_from_event(context, event)
            self._update_status_text(context, "")  # Update for snap status

            if self._snap_state.snap_type == 'VERTEX':
                context.window.cursor_set('HAND')
            elif self._snap_state.snap_type == 'MIDPOINT':
                context.window.cursor_set('PAINT_CROSS')
            else:
                context.window.cursor_set('CROSSHAIR')

            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            return self._handle_left_click(context, event)

        if event.type == "RET" and event.value == "PRESS":
            return self._handle_confirm_numeric(context)

        if event.type == "ESC":
            self._finish(context, message="Line tool cancelled")
            return {"FINISHED"}

        if event.type in {"X", "Y", "Z"} and event.value == "PRESS":
            self._set_constraint(event.type, shift=event.shift)
            self._preview_world = self._constrained_point_from_event(context, event)
            return {"RUNNING_MODAL"}

        if event.type == "SHIFT" and event.value == "RELEASE":
            if self._constraint.exclude_axis:
                self._constraint.exclude_axis = False
                self._update_status_text(context, "Axis constraint reset to single axis")
            return {"RUNNING_MODAL"}

        if event.type == "BACK_SPACE" and event.value == "PRESS":
            if self._numeric_input:
                self._numeric_input = self._numeric_input[:-1]
                self._update_status_text(context, f"Input: {self._numeric_input or '…'}")
            return {"RUNNING_MODAL"}

        if event.value == "PRESS" and event.type in self._accepted_numeric(event):
            char = self._event_to_char(event)
            if char:
                self._numeric_input += char
                self._update_status_text(context, f"Input: {self._numeric_input}")
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def cancel(self, context: Context):
        self._finish(context, message="Line tool cancelled")

    # ----- event handling ----------------------------------------------------
    def _handle_left_click(self, context: Context, event: Event):
        world_point = self._constrained_point_from_event(context, event)
        if world_point is None:
            return {"RUNNING_MODAL"}

        local_point = self._to_local(world_point)

        if self._start_local is None:
            self._start_vert = self._bm.verts.new(local_point)
            self._start_local = local_point.copy()
            self._start_world = world_point.copy()
            self._bm.verts.ensure_lookup_table()
            self._numeric_input = ""
            self._update_status_text(context, "First point set")
        else:
            end_local = local_point
            end_world = world_point
            if self._numeric_input:
                numeric_world = self._resolve_numeric_input()
                if numeric_world:
                    end_world = numeric_world
                    end_local = self._to_local(end_world)
            new_vert = self._bm.verts.new(end_local)
            self._bm.edges.new((self._start_vert, new_vert))
            self._bm.verts.ensure_lookup_table()
            self._bm.edges.ensure_lookup_table()
            bmesh.update_edit_mesh(self._active_obj.data, loop_triangles=False)

            self._start_vert = new_vert
            self._start_local = end_local.copy()
            self._start_world = end_world.copy()
            self._numeric_input = ""
            self._update_status_text(context, "Segment created – continue or press Esc")

        bmesh.update_edit_mesh(self._active_obj.data, loop_triangles=False)
        return {"RUNNING_MODAL"}

    def _handle_confirm_numeric(self, context: Context):
        if not self._start_local or not self._numeric_input:
            self._update_status_text(context, "No numeric input to confirm")
            return {"RUNNING_MODAL"}

        target_world = self._resolve_numeric_input()
        if target_world is None:
            self._update_status_text(context, "Invalid numeric input")
            self._numeric_input = ""
            return {"RUNNING_MODAL"}

        end_local = self._to_local(target_world)
        new_vert = self._bm.verts.new(end_local)
        self._bm.edges.new((self._start_vert, new_vert))
        self._bm.verts.ensure_lookup_table()
        self._bm.edges.ensure_lookup_table()
        bmesh.update_edit_mesh(self._active_obj.data, loop_triangles=False)

        self._start_vert = new_vert
        self._start_local = end_local.copy()
        self._start_world = target_world.copy()
        self._numeric_input = ""
        self._update_status_text(context, "Segment created from numeric input")

        return {"RUNNING_MODAL"}

    # ----- helpers -----------------------------------------------------------
    def _finish(self, context: Context, message: str):
        context.window.cursor_set('DEFAULT')
        if self._bm:
            bmesh.update_edit_mesh(context.edit_object.data, loop_triangles=False)

        self._update_status_text(context, message)
        self._constraint = ConstraintState()
        self._numeric_input = ""
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def _reset_state(self):
        self._bm = None
        self._active_obj = None
        self._matrix_world = None
        self._matrix_world_inv = None
        self._start_vert = None
        self._start_local = None
        self._start_world = None
        self._numeric_input = ""
        self._constraint = ConstraintState()
        self._snap_state = SnapState()
        self._preview_world = None
        self._mouse_region = (0, 0)

    def _ensure_edit_mesh(self, context: Context):
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass

        obj = context.active_object
        if obj is None or obj.type != "MESH":
            mesh = bpy.data.meshes.new("CADSketchMesh")
            obj = bpy.data.objects.new("CADSketch", mesh)
            context.scene.collection.objects.link(obj)
            context.view_layer.objects.active = obj
        else:
            context.view_layer.objects.active = obj

        if not obj.select_get():
            obj.select_set(True)

        if context.mode != "EDIT_MESH":
            bpy.ops.object.mode_set(mode="EDIT")

    def _constrained_point_from_event(self, context: Context, event: Event) -> Optional[Vector]:
        raw_world = self._location_from_event(context, event)
        if raw_world is None:
            return None
        if self._start_local is None:
            return raw_world
        constrained = self._apply_constraint_world(raw_world)
        return constrained

    def _apply_constraint_world(self, world_point: Vector) -> Vector:
        local_point = self._to_local(world_point)
        start_local = self._start_local
        constrained_local = local_point.copy()

        if self._constraint.axis:
            axis = self._constraint.axis
            idx = "XYZ".index(axis)
            if self._constraint.exclude_axis:
                constrained_local[idx] = start_local[idx]
            else:
                constrained_local = start_local.copy()
                constrained_local[idx] = local_point[idx]

        constrained_world = self._active_obj.matrix_world @ constrained_local
        return constrained_world

    def _resolve_numeric_input(self) -> Optional[Vector]:
        if not self._start_local:
            return None

        try:
            value = float(self._numeric_input)
        except ValueError:
            return None

        if self._preview_world is None:
            return None

        start_world = self._start_world
        direction = self._preview_world - start_world
        if direction.length_squared == 0.0:
            axis = self._constraint.axis or "X"
            direction = AXIS_VECTORS[axis].copy()
            if self._constraint.exclude_axis:
                others = [v for key, v in AXIS_VECTORS.items() if key != axis]
                direction = (others[0] + others[1]).normalized()
        else:
            direction.normalize()

        target_world = start_world + direction * value
        if self._constraint.axis:
            target_world = self._apply_constraint_world(target_world)
        return target_world

    def _set_constraint(self, axis: str, shift: bool):
        if axis not in {"X", "Y", "Z"}:
            return
        self._constraint.axis = axis
        self._constraint.exclude_axis = shift

    def _find_snap_point(self, context: Context, event: Event) -> Optional[Vector]:
        """Find the nearest snap point (vertex or edge midpoint) to the mouse cursor."""
        region = context.region
        rv3d = context.space_data.region_3d
        mouse_coord = Vector((event.mouse_region_x, event.mouse_region_y))
        snap_radius = 10.0

        # --- Vertex Snapping (Highest Priority) ---
        best_vert_dist = snap_radius
        snap_vert = None
        for v in self._bm.verts:
            if not v.hide:
                world_pos = self._matrix_world @ v.co
                screen_pos = view3d_utils.location_3d_to_region_2d(region, rv3d, world_pos)
                if screen_pos:
                    dist = (mouse_coord - screen_pos).length
                    if dist < best_vert_dist:
                        best_vert_dist = dist
                        snap_vert = v

        if snap_vert:
            self._snap_state.snap_type = 'VERTEX'
            self._snap_state.target_world = self._matrix_world @ snap_vert.co
            self._snap_state.target_screen = view3d_utils.location_3d_to_region_2d(
                region, rv3d, self._snap_state.target_world
            )
            return self._snap_state.target_world

        # --- Edge Midpoint Snapping ---
        best_midpoint_dist = snap_radius
        snap_midpoint_world = None
        for e in self._bm.edges:
            if not e.hide:
                midpoint_local = (e.verts[0].co + e.verts[1].co) / 2.0
                midpoint_world = self._matrix_world @ midpoint_local
                screen_pos = view3d_utils.location_3d_to_region_2d(region, rv3d, midpoint_world)
                if screen_pos:
                    dist = (mouse_coord - screen_pos).length
                    if dist < best_midpoint_dist:
                        best_midpoint_dist = dist
                        snap_midpoint_world = midpoint_world

        if snap_midpoint_world:
            self._snap_state.snap_type = 'MIDPOINT'
            self._snap_state.target_world = snap_midpoint_world
            self._snap_state.target_screen = view3d_utils.location_3d_to_region_2d(
                region, rv3d, self._snap_state.target_world
            )
            return self._snap_state.target_world

        # --- No snap found ---
        self._snap_state = SnapState()
        return None

    def _location_from_event(self, context: Context, event: Event) -> Optional[Vector]:
        snap_location = self._find_snap_point(context, event)
        if snap_location:
            return snap_location

        region = context.region
        rv3d = context.space_data.region_3d
        coord = (event.mouse_region_x, event.mouse_region_y)
        view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        ray_target = ray_origin + view_vector * 1000.0

        depsgraph = context.evaluated_depsgraph_get()
        hit, location, *_ = context.scene.ray_cast(depsgraph, ray_origin, view_vector)
        if hit:
            return location

        plane_point = context.scene.cursor.location
        plane_normal = (rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))).normalized()
        intersect = geom.intersect_line_plane(ray_origin, ray_target, plane_point, plane_normal)
        if intersect is not None:
            return intersect

        return ray_origin

    def _to_local(self, world_point: Vector) -> Vector:
        return self._matrix_world_inv @ world_point

    def _accepted_numeric(self, event: Event):
        return {
            "ZERO",
            "ONE",
            "TWO",
            "THREE",
            "FOUR",
            "FIVE",
            "SIX",
            "SEVEN",
            "EIGHT",
            "NINE",
            "PERIOD",
            "MINUS",
        }

    def _event_to_char(self, event: Event) -> Optional[str]:
        mapping = {
            "ZERO": "0",
            "ONE": "1",
            "TWO": "2",
            "THREE": "3",
            "FOUR": "4",
            "FIVE": "5",
            "SIX": "6",
            "SEVEN": "7",
            "EIGHT": "8",
            "NINE": "9",
            "PERIOD": ".",
            "MINUS": "-" if not event.shift else None,
        }
        return mapping.get(event.type)

    def _update_status_text(self, context: Context, message: str):
        constraint_label = self._constraint.label()
        snap_label = self._snap_state.label()

        parts = []
        if message:
            parts.append(f"[Line] {message}")

        if self._start_local:
            parts.append(f"Axis: {constraint_label}")

        parts.append(f"Snap: {snap_label}")

        if self._numeric_input:
            parts.append(f"Input: {self._numeric_input}")

        status = " | ".join(parts)
        context.area.header_text_set(status)
