import bpy
from . import scanner


_last_result: scanner.ScanResult | None = None


def get_last_result() -> scanner.ScanResult | None:
    return _last_result


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

class GNAU_OT_ScanStrings(bpy.types.Operator):
    """Scan geometry node modifiers for Named Attribute and Store Named Attribute nodes"""

    bl_idname = "gnau.scan_strings"
    bl_label = "Scan Attributes"
    bl_options = {"REGISTER"}

    scope: bpy.props.EnumProperty(
        name="Scope",
        items=[
            ("SCENE",    "Whole Scene",      "Scan all objects in the scene"),
            ("SELECTED", "Selected Objects", "Scan only selected objects"),
            ("ACTIVE",   "Active Object",    "Scan only the active object"),
        ],
        default="SCENE",
    )

    def execute(self, context):
        global _last_result

        if self.scope == "ACTIVE":
            obj = context.active_object
            if obj is None:
                self.report({"WARNING"}, "No active object.")
                return {"CANCELLED"}
            result = scanner.scan_object(obj)
        elif self.scope == "SELECTED":
            result = scanner.scan_scene(context, selected_only=True)
        else:
            result = scanner.scan_scene(context, selected_only=False)

        _last_result = result
        unique = len(result.unique_names())
        total  = len(result.hits)
        self.report({"INFO"}, f"Found {unique} unique attributes across {total} nodes.")

        for area in context.screen.areas:
            area.tag_redraw()

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

class GNAU_OT_ClearResults(bpy.types.Operator):
    """Clear the last scan results"""

    bl_idname = "gnau.clear_results"
    bl_label = "Clear Results"
    bl_options = {"REGISTER"}

    def execute(self, context):
        global _last_result
        _last_result = None
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


class GNAU_OT_GetSelectedNodeString(bpy.types.Operator):
    """Resolve and display the effective string for the active node"""

    bl_idname = "gnau.get_selected_node_string"
    bl_label = "Get Selected Node String"
    bl_options = {"REGISTER"}

    def execute(self, context):
        snode = context.space_data
        if snode is None or snode.type != "NODE_EDITOR":
            self.report({"WARNING"}, "Not in a Node Editor.")
            return {"CANCELLED"}

        edit_tree = getattr(snode, "edit_tree", None) or snode.node_tree
        if edit_tree is None:
            self.report({"WARNING"}, "No active node tree.")
            return {"CANCELLED"}

        node = edit_tree.nodes.active
        if node is None:
            self.report({"WARNING"}, "No active node selected.")
            return {"CANCELLED"}

        # Build parent stack from the current node editor path.
        # Format required by scanner resolver: [(parent_tree, group_node), ...]
        parent_stack = []
        path = getattr(snode, "path", None)
        if path is not None and len(path) > 1:
            for idx in range(1, len(path)):
                parent_tree = path[idx - 1].node_tree
                group_node = path[idx].node
                if parent_tree is not None and group_node is not None:
                    parent_stack.append((parent_tree, group_node))

        socket_name, value = scanner.resolve_first_string_input(node, edit_tree, parent_stack)

        wm = context.window_manager
        if socket_name is None:
            wm.gnau_selected_node_string = ""
            wm.gnau_selected_node_socket = ""
            self.report({"WARNING"}, f"Node '{node.name}' has no string input socket.")
            return {"CANCELLED"}

        if value:
            wm.gnau_selected_node_socket = socket_name
            wm.gnau_selected_node_string = value
            self.report({"INFO"}, f"{node.name} -> {socket_name}: \"{value}\"")
        else:
            wm.gnau_selected_node_socket = socket_name
            wm.gnau_selected_node_string = ""
            self.report({"WARNING"}, f"{node.name} -> {socket_name}: unresolved/empty")

        for area in context.screen.areas:
            area.tag_redraw()

        return {"FINISHED"}


class GNAU_OT_UseSelectedStringAsFilter(bpy.types.Operator):
    """Use the manually retrieved string as search filter"""

    bl_idname = "gnau.use_selected_string_as_filter"
    bl_label = "Use String As Search"
    bl_options = {"REGISTER"}

    def execute(self, context):
        wm = context.window_manager
        value = wm.gnau_selected_node_string.strip()
        if not value:
            self.report({"WARNING"}, "No retrieved string available.")
            return {"CANCELLED"}

        wm.gnau_filter_text = value
        self.report({"INFO"}, f"Search filter set to: \"{value}\"")

        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


class GNAU_OT_ClearSearchFilter(bpy.types.Operator):
    """Clear the search filter text"""

    bl_idname = "gnau.clear_search_filter"
    bl_label = "Clear Search"
    bl_options = {"REGISTER"}

    def execute(self, context):
        context.window_manager.gnau_filter_text = ""
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Focus Node — navigate the node editor to a specific node in any tree depth
# ---------------------------------------------------------------------------

class GNAU_OT_FocusNode(bpy.types.Operator):
    """Navigate the Geometry Node editor to this node, entering groups as needed"""

    bl_idname = "gnau.focus_node"
    bl_label = "Focus Node"
    bl_options = {"REGISTER"}

    # Passed from the UI button
    object_name:    bpy.props.StringProperty()
    modifier_name:  bpy.props.StringProperty()
    node_tree_name: bpy.props.StringProperty()
    node_name:      bpy.props.StringProperty()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_node_editor(context):
        """Return (area, region, space) for the first Node Editor found."""
        for area in context.screen.areas:
            if area.type == "NODE_EDITOR":
                space = area.spaces.active
                # The WINDOW region is the main canvas — needed for view_selected
                for region in area.regions:
                    if region.type == "WINDOW":
                        return area, region, space
        return None, None, None

    @staticmethod
    def _find_group_node_for_tree(tree: bpy.types.NodeTree, target_tree: bpy.types.NodeTree) -> bpy.types.Node | None:
        """Find a group node inside `tree` whose node_tree is `target_tree`."""
        for node in tree.nodes:
            if hasattr(node, "node_tree") and node.node_tree == target_tree:
                return node
        return None

    @staticmethod
    def _build_path_to_tree(
        root_tree: bpy.types.NodeTree,
        target_tree: bpy.types.NodeTree,
        visited: set,
    ) -> list[bpy.types.Node] | None:
        """
        BFS to find the chain of group nodes from root_tree down to target_tree.
        Returns a list of group nodes to enter in order, or None if not found.
        """
        if root_tree == target_tree:
            return []

        from collections import deque
        queue = deque()
        queue.append((root_tree, []))
        visited_trees = {root_tree.name}

        while queue:
            current_tree, path = queue.popleft()
            for node in current_tree.nodes:
                if not hasattr(node, "node_tree") or node.node_tree is None:
                    continue
                child_tree = node.node_tree
                if child_tree == target_tree:
                    return path + [node]
                if child_tree.name not in visited_trees:
                    visited_trees.add(child_tree.name)
                    queue.append((child_tree, path + [node]))

        return None

    # ------------------------------------------------------------------

    def execute(self, context):
        # ── Resolve data ─────────────────────────────────────────────
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            self.report({"WARNING"}, f"Object '{self.object_name}' not found.")
            return {"CANCELLED"}

        modifier = obj.modifiers.get(self.modifier_name)
        if modifier is None or modifier.type != "NODES" or modifier.node_group is None:
            self.report({"WARNING"}, f"Modifier '{self.modifier_name}' not found or has no node group.")
            return {"CANCELLED"}

        root_tree   = modifier.node_group
        target_tree = bpy.data.node_groups.get(self.node_tree_name)
        if target_tree is None:
            self.report({"WARNING"}, f"Node tree '{self.node_tree_name}' not found.")
            return {"CANCELLED"}

        target_node = target_tree.nodes.get(self.node_name)
        if target_node is None:
            self.report({"WARNING"}, f"Node '{self.node_name}' not found.")
            return {"CANCELLED"}

        # ── Find the node editor ──────────────────────────────────────
        area, region, snode = self._find_node_editor(context)
        if snode is None:
            self.report({"WARNING"}, "No Node Editor area found on screen.")
            return {"CANCELLED"}

        # ── Make the right object active ──────────────────────────────
        if context.active_object != obj:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            context.view_layer.objects.active = obj

        # ── Build the group-node path from root → target tree ─────────
        path = self._build_path_to_tree(root_tree, target_tree, set())
        if path is None:
            self.report({"WARNING"}, f"Could not find a path to '{self.node_tree_name}'.")
            return {"CANCELLED"}

        with context.temp_override(area=area, region=region, space_data=snode):

            # 1. Unpin and point the editor at the modifier's root tree.
            snode.pin = False
            snode.node_tree = root_tree

            # 2. Collapse the path stack back to just the root level.
            #    snode.path is the internal stack Blender uses to track which
            #    group you are currently editing.  Clearing it and re-adding
            #    only the root is the reliable way to reset nesting state.
            snode.path.clear()
            snode.path.start(root_tree)

            # 3. Walk down into each group node in the path.
            for group_node in path:
                # Deselect everything in the current edit tree, then activate
                # the group node we want to enter.
                edit_tree = snode.edit_tree
                for n in edit_tree.nodes:
                    n.select = False
                group_node.select = True
                edit_tree.nodes.active = group_node

                # Push this group onto the path stack (= "enter group").
                snode.path.append(node_tree=group_node.node_tree, node=group_node)

            # 4. We are now inside target_tree.  Select only the target node.
            edit_tree = snode.edit_tree
            for n in edit_tree.nodes:
                n.select = False
            target_node.select = True
            edit_tree.nodes.active = target_node

            # 5. Frame the view on the selected node.
            bpy.ops.node.view_selected()

        self.report({"INFO"}, f"Focused on '{self.node_name}'.")
        return {"FINISHED"}


classes = (
    GNAU_OT_ScanStrings,
    GNAU_OT_ClearResults,
    GNAU_OT_GetSelectedNodeString,
    GNAU_OT_UseSelectedStringAsFilter,
    GNAU_OT_ClearSearchFilter,
    GNAU_OT_FocusNode,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
