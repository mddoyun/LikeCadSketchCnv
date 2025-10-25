"""CAD-style trim tool for LikeCadSketch."""

import bpy
import bmesh
from bpy.types import Context, Event, Operator
from bpy_extras import view3d_utils
from mathutils import Vector, geometry


class VIEW3D_OT_cad_trim(Operator):
    """Trim edges with CAD-like precision."""

    bl_idname = "view3d.cad_trim"
    bl_label = "CAD Trim"
    bl_description = "Trim edges based on cutting edges"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    def invoke(self, context: Context, event: Event):
        if context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "3D View only")
            return {"CANCELLED"}

        self._state = 'SELECT_CUTTING_EDGES'
        self._cutting_edges = []
        self._active_obj = context.edit_object
        if not self._active_obj or self._active_obj.type != 'MESH':
            self.report({"WARNING"}, "No active mesh object found.")
            return {"CANCELLED"}

        self._bm = bmesh.from_edit_mesh(self._active_obj.data)
        self._bm.edges.ensure_lookup_table()

        self.report({"INFO"}, "CAD Trim tool activated. Select cutting edges (Left-click) or Right-click to confirm.")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Event):
        context.area.tag_redraw()

        if event.type in {"ESC"}:
            self.report({"INFO"}, "CAD Trim tool cancelled.")
            return {"CANCELLED"}

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

        if self._state == 'SELECT_CUTTING_EDGES':
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                self._select_cutting_edge(context, event)
            elif event.type == "RIGHTMOUSE" and event.value == "PRESS":
                if not self._cutting_edges:
                    self.report({"WARNING"}, "No cutting edges selected. Right-click again to cancel.")
                    return {"CANCELLED"}
                self._state = 'SELECT_EDGES_TO_TRIM'
                self.report({"INFO"}, "Cutting edges confirmed. Select edges to trim (Left-click).")

        elif self._state == 'SELECT_EDGES_TO_TRIM':
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                self._trim_edge(context, event)
            elif event.type == "RIGHTMOUSE" and event.value == "PRESS":
                self.report({"INFO"}, "CAD Trim tool finished.")
                return {"FINISHED"}

        return {"RUNNING_MODAL"}

    def execute(self, context: Context):
        self.report({"INFO"}, "CAD Trim tool finished.")
        return {"FINISHED"}

    @staticmethod
    def _closest_point_on_line_segment(p: Vector, a: Vector, b: Vector) -> Vector:
        ab = b - a
        ap = p - a
        proj = ap.dot(ab)
        ab_len_sq = ab.length_squared

        if ab_len_sq == 0.0:
            return a

        t = proj / ab_len_sq
        t = max(0.0, min(1.0, t))

        closest_point = a + t * ab
        return closest_point

    def _ray_cast_edge(self, context: Context, event: Event):
        region = context.region
        rv3d = context.space_data.region_3d
        mouse_coord = Vector((event.mouse_region_x, event.mouse_region_y))

        bmesh.update_edit_mesh(self._active_obj.data, loop_triangles=False, destructive=False)
        self._bm.edges.ensure_lookup_table()

        closest_screen_dist_sq = float('inf')
        closest_bmedge = None
        screen_threshold_sq = 10 * 10

        for edge in self._bm.edges:
            if edge.hide:
                continue
            
            v1_world = self._active_obj.matrix_world @ edge.verts[0].co
            v2_world = self._active_obj.matrix_world @ edge.verts[1].co

            v1_screen = view3d_utils.location_3d_to_region_2d(region, rv3d, v1_world)
            v2_screen = view3d_utils.location_3d_to_region_2d(region, rv3d, v2_world)

            if v1_screen is None or v2_screen is None:
                continue

            closest_point_on_screen_edge = self._closest_point_on_line_segment(
                mouse_coord, v1_screen, v2_screen
            )
            dist_sq = (closest_point_on_screen_edge - mouse_coord).length_squared
            
            if dist_sq < closest_screen_dist_sq and dist_sq < screen_threshold_sq:
                closest_screen_dist_sq = dist_sq
                closest_bmedge = edge
        
        if closest_bmedge:
            ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse_coord)
            ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse_coord)
            ray_target = ray_origin + ray_vector * 10000

            v1_world = self._active_obj.matrix_world @ closest_bmedge.verts[0].co
            v2_world = self._active_obj.matrix_world @ closest_bmedge.verts[1].co

            _, intersect_pt_on_edge = geometry.intersect_line_line(
                ray_origin, ray_target, v1_world, v2_world
            )
            
            return closest_bmedge, intersect_pt_on_edge
            
        return None, None

    def _select_cutting_edge(self, context: Context, event: Event):
        edge, _ = self._ray_cast_edge(context, event)
        if edge:
            if edge not in self._cutting_edges:
                self._cutting_edges.append(edge)
                edge.select_set(True)
                self.report({"INFO"}, f"Cutting edge {edge.index} selected: {len(self._cutting_edges)} edges.")
            else:
                self._cutting_edges.remove(edge)
                edge.select_set(False)
                self.report({"INFO"}, f"Cutting edge {edge.index} deselected: {len(self._cutting_edges)} edges.")
        else:
            self.report({"WARNING"}, "No edge found under mouse.")

    def _trim_edge(self, context: Context, event: Event):
        edge_to_trim, mouse_world_loc = self._ray_cast_edge(context, event)
        if not edge_to_trim:
            self.report({"WARNING"}, "No edge found under mouse to trim.")
            return

        original_v1_co = edge_to_trim.verts[0].co.copy()
        original_v2_co = edge_to_trim.verts[1].co.copy()

        intersections_with_factors = []
        for cutting_edge in self._cutting_edges:
            if cutting_edge == edge_to_trim:
                continue

            intersection_point = self._get_intersection_point(edge_to_trim, cutting_edge)
            if intersection_point:
                edge_vec = original_v2_co - original_v1_co
                if edge_vec.length_squared == 0.0:
                    continue
                factor = (intersection_point - original_v1_co).dot(edge_vec) / edge_vec.length_squared
                intersections_with_factors.append((factor, intersection_point))

        if not intersections_with_factors:
            self.report({"INFO"}, "No intersections found with cutting edges.")
            return

        intersections_with_factors.sort(key=lambda x: x[0])
        intersection_points = [p for f, p in intersections_with_factors]

        if not edge_to_trim.is_valid:
            self.report({"ERROR"}, "Edge to trim is invalid before subdivision.")
            return

        num_cuts = len(intersection_points)
        if num_cuts == 0:
            return

        ret = bmesh.ops.subdivide_edges(self._bm, edges=[edge_to_trim], cuts=num_cuts)
        
        new_verts = [v for v in ret['geom_split'] if isinstance(v, bmesh.types.BMVert)]
        edge_vec_norm = (original_v2_co - original_v1_co).normalized()
        new_verts.sort(key=lambda v: (v.co - original_v1_co).dot(edge_vec_norm))

        if len(new_verts) == num_cuts:
            for i, vert in enumerate(new_verts):
                vert.co = intersection_points[i]
        else:
            self.report({"WARNING"}, "Mismatch in number of new vertices and intersection points.")

        all_new_edges = [e for e in ret['geom_split'] if isinstance(e, bmesh.types.BMEdge)]
        if not all_new_edges:
            self.report({"WARNING"}, "Edge split did not result in any new edges.")
            return

        edge_to_delete = None
        min_dist_sq_to_click = float('inf')

        for edge_segment in all_new_edges:
            if not edge_segment.is_valid:
                continue

            v1_world = self._active_obj.matrix_world @ edge_segment.verts[0].co
            v2_world = self._active_obj.matrix_world @ edge_segment.verts[1].co
            
            closest_point_on_segment = self._closest_point_on_line_segment(
                mouse_world_loc, v1_world, v2_world
            )
            dist_sq = (closest_point_on_segment - mouse_world_loc).length_squared
            
            if dist_sq < min_dist_sq_to_click:
                min_dist_sq_to_click = dist_sq
                edge_to_delete = edge_segment
        
        if edge_to_delete and edge_to_delete.is_valid:
            bmesh.ops.delete(self._bm, geom=[edge_to_delete], context='EDGES')
            self.report({"INFO"}, "Edge successfully trimmed.")
        else:
            self.report({"WARNING"}, "Could not determine which segment to delete.")

        bmesh.update_edit_mesh(self._active_obj.data)
        self._bm.edges.ensure_lookup_table()
        context.area.tag_redraw()

    @classmethod
    def _get_intersection_point(cls, edge1, edge2) -> Vector | None:
        obj = bpy.context.edit_object
        matrix_world = obj.matrix_world

        e1_v1_world = matrix_world @ edge1.verts[0].co
        e1_v2_world = matrix_world @ edge1.verts[1].co
        e2_v1_world = matrix_world @ edge2.verts[0].co
        e2_v2_world = matrix_world @ edge2.verts[1].co

        p1, p2 = geometry.intersect_line_line(e1_v1_world, e1_v2_world, e2_v1_world, e2_v2_world)

        EPSILON = 0.0001

        if (p1 - p2).length_squared < EPSILON:
            on_segment1 = (p1 - e1_v1_world).length_squared + (p1 - e1_v2_world).length_squared \
                          <= (e1_v1_world - e1_v2_world).length_squared + EPSILON
            on_segment2 = (p1 - e2_v1_world).length_squared + (p1 - e2_v2_world).length_squared \
                          <= (e2_v1_world - e2_v2_world).length_squared + EPSILON

            if on_segment1 and on_segment2:
                return p1
        return None
