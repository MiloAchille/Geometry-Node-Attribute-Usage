"""
Scanner: walks a geometry node tree recursively (including all group nodes)
and collects every attribute name used in:
  - GeometryNodeInputNamedAttribute  (READ)
  - GeometryNodeStoreNamedAttribute  (WRITE)
"""

from __future__ import annotations
import bpy
from collections import defaultdict
from dataclasses import dataclass, field


READ_NODE  = "GeometryNodeInputNamedAttribute"   # "Named Attribute"
WRITE_NODE = "GeometryNodeStoreNamedAttribute"   # "Store Named Attribute"

GROUP_NODE_TYPES = {"GeometryNodeGroup", "ShaderNodeGroup"}


@dataclass
class AttributeHit:
    """One occurrence of a named attribute in the node graph."""
    attribute_name: str
    mode: str           # "READ" | "WRITE"
    node_tree_name: str
    node_name: str
    modifier_name: str
    object_name: str


@dataclass
class ScanResult:
    hits: list[AttributeHit] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def unique_names(self) -> list[str]:
        seen = []
        for h in self.hits:
            if h.attribute_name not in seen:
                seen.append(h.attribute_name)
        return seen

    def hits_for(self, name: str) -> list[AttributeHit]:
        return [h for h in self.hits if h.attribute_name == name]

    def modes_for(self, name: str) -> set[str]:
        return {h.mode for h in self.hits_for(name)}

    def count_for(self, name: str) -> int:
        return len(self.hits_for(name))

    def sorted_names(self) -> list[str]:
        """Unique names sorted by total hit count, descending."""
        return sorted(self.unique_names(), key=lambda n: self.count_for(n), reverse=True)


# ---------------------------------------------------------------------------
# Internal traversal
# ---------------------------------------------------------------------------

def _resolve_string_value(
    from_node: bpy.types.Node,
    from_socket: bpy.types.NodeSocket,
    current_tree: bpy.types.NodeTree,
    parent_stack: list,   # list of (parent_tree, group_node) tuples, outermost first
    depth: int,
) -> str | None:
    """
    Core recursive resolver. Traces a string value backwards through any
    combination of:
      - Reroute nodes (transparent pass-through)
      - String literal nodes  (FunctionNodeInputString)
      - Group nodes  (descend into the group tree, find what drives the input)
      - NodeGroupInput  (ascend back to the parent tree via parent_stack)
      - NodeGroupOutput (ascend back out of a group when following outputs)
    """
    if depth > 32:
        return None

    # ── Reroute: transparent, just follow its own input ──────────────
    if from_node.bl_idname == "NodeReroute":
        if from_node.inputs and from_node.inputs[0].is_linked:
            link2 = from_node.inputs[0].links[0]
            return _resolve_string_value(
                link2.from_node, link2.from_socket,
                current_tree, parent_stack, depth + 1,
            )
        # Unlinked reroute — read its default (rare but possible)
        try:
            val = from_node.inputs[0].default_value
            if isinstance(val, str) and val.strip():
                return val.strip()
        except Exception:
            pass
        return None

    # ── String literal node ──────────────────────────────────────────
    if from_node.bl_idname == "FunctionNodeInputString":
        try:
            val = from_node.string
            return val.strip() if val and val.strip() else None
        except Exception:
            return None

    # ── Group Input node: the value comes from OUTSIDE the current group ─
    # We need to climb back to the parent tree and find what's connected
    # to the corresponding input socket on the group node that called us.
    if from_node.bl_idname == "NodeGroupInput":
        if not parent_stack:
            # At the very top level — value is set by the modifier panel
            # Try reading it as a default on the group-input output socket
            try:
                val = from_socket.default_value
                if isinstance(val, str) and val.strip():
                    return val.strip()
            except Exception:
                pass
            return None

        parent_tree, group_node = parent_stack[-1]

        # Match by socket identifier (most robust across renames)
        identifier = from_socket.identifier
        for grp_in_socket in group_node.inputs:
            if grp_in_socket.identifier == identifier:
                if grp_in_socket.is_linked:
                    link2 = grp_in_socket.links[0]
                    return _resolve_string_value(
                        link2.from_node, link2.from_socket,
                        parent_tree, parent_stack[:-1], depth + 1,
                    )
                else:
                    try:
                        val = grp_in_socket.default_value
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                    except Exception:
                        pass
                return None

        # Fallback: match by position
        src_idx = list(from_node.outputs).index(from_socket) if from_socket in from_node.outputs else -1
        if src_idx >= 0 and src_idx < len(group_node.inputs):
            grp_in_socket = group_node.inputs[src_idx]
            if grp_in_socket.is_linked:
                link2 = grp_in_socket.links[0]
                return _resolve_string_value(
                    link2.from_node, link2.from_socket,
                    parent_tree, parent_stack[:-1], depth + 1,
                )
        return None

    # ── Group node: the value comes from INSIDE the group tree ──────
    # from_socket is an *output* of the group node → find the Group Output
    # node inside the group tree and follow what drives that output.
    if from_node.bl_idname in ("GeometryNodeGroup", "ShaderNodeGroup") \
            and from_node.node_tree is not None:
        inner_tree = from_node.node_tree
        identifier  = from_socket.identifier

        # Find the NodeGroupOutput node inside inner_tree
        for inner_node in inner_tree.nodes:
            if inner_node.bl_idname == "NodeGroupOutput":
                for out_sock in inner_node.inputs:
                    if out_sock.identifier == identifier:
                        if out_sock.is_linked:
                            link2 = out_sock.links[0]
                            return _resolve_string_value(
                                link2.from_node, link2.from_socket,
                                inner_tree,
                                parent_stack + [(current_tree, from_node)],
                                depth + 1,
                            )
                        else:
                            try:
                                val = out_sock.default_value
                                if isinstance(val, str) and val.strip():
                                    return val.strip()
                            except Exception:
                                pass
                        return None
        return None

    # ── Plain socket with a direct value ────────────────────────────
    try:
        val = from_socket.default_value
        if isinstance(val, str) and val.strip():
            return val.strip()
    except Exception:
        pass

    # The socket itself might be linked further — keep following
    if from_socket.is_linked:
        link2 = from_socket.links[0]
        return _resolve_string_value(
            link2.from_node, link2.from_socket,
            current_tree, parent_stack, depth + 1,
        )

    return None


def _resolve_string_socket(
    socket: bpy.types.NodeSocket,
    current_tree: bpy.types.NodeTree,
    parent_stack: list | None = None,
) -> str | None:
    """
    Entry point: resolve the actual string value feeding into an input socket.
    parent_stack is a list of (tree, group_node) pairs from outermost inward.
    """
    if parent_stack is None:
        parent_stack = []

    if not socket.is_linked:
        try:
            val = socket.default_value
            if isinstance(val, str) and val.strip():
                return val.strip()
        except Exception:
            pass
        return None

    link = socket.links[0]
    return _resolve_string_value(
        link.from_node, link.from_socket,
        current_tree, parent_stack, depth=0,
    )


def _get_attribute_name_from_node(
    node: bpy.types.Node,
    current_tree: bpy.types.NodeTree,
    parent_stack: list | None = None,
) -> str | None:
    """
    Extract the attribute name string from a Named Attribute or
    Store Named Attribute node, following any links to their true source.
    """
    for socket in node.inputs:
        if socket.type == "STRING":
            val = _resolve_string_socket(socket, current_tree, parent_stack)
            if val:
                return val
    return None


def resolve_first_string_input(
    node: bpy.types.Node,
    current_tree: bpy.types.NodeTree,
    parent_stack: list | None = None,
) -> tuple[str | None, str | None]:
    """
    Resolve the effective string value for the first string input found on a node.
    Returns (socket_name, value). Value is None if unresolved/empty.
    """
    for socket in node.inputs:
        if socket.type == "STRING":
            val = _resolve_string_socket(socket, current_tree, parent_stack)
            return socket.name, val
    return None, None


def _walk_tree(
    tree: bpy.types.NodeTree,
    modifier_name: str,
    object_name: str,
    visited: set[str],
    result: ScanResult,
    parent_stack: list | None = None,
) -> None:
    if tree.name in visited:
        return
    visited.add(tree.name)

    if parent_stack is None:
        parent_stack = []

    for node in tree.nodes:
        if node.bl_idname == READ_NODE:
            name = _get_attribute_name_from_node(node, tree, parent_stack)
            if name:
                result.hits.append(AttributeHit(
                    attribute_name=name,
                    mode="READ",
                    node_tree_name=tree.name,
                    node_name=node.name,
                    modifier_name=modifier_name,
                    object_name=object_name,
                ))

        elif node.bl_idname == WRITE_NODE:
            name = _get_attribute_name_from_node(node, tree, parent_stack)
            if name:
                result.hits.append(AttributeHit(
                    attribute_name=name,
                    mode="WRITE",
                    node_tree_name=tree.name,
                    node_name=node.name,
                    modifier_name=modifier_name,
                    object_name=object_name,
                ))

        # Recurse into group nodes, extending the parent stack
        if node.bl_idname in GROUP_NODE_TYPES and node.node_tree is not None:
            _walk_tree(
                node.node_tree,
                modifier_name,
                object_name,
                visited,
                result,
                parent_stack + [(tree, node)],
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_modifier(obj: bpy.types.Object, mod: bpy.types.NodesModifier) -> ScanResult:
    result = ScanResult()
    if mod.node_group is None:
        return result
    _walk_tree(mod.node_group, mod.name, obj.name, set(), result)
    return result


def scan_object(obj: bpy.types.Object) -> ScanResult:
    combined = ScanResult()
    for mod in obj.modifiers:
        if mod.type == "NODES":
            partial = scan_modifier(obj, mod)
            combined.hits.extend(partial.hits)
    return combined


def scan_scene(context: bpy.types.Context, selected_only: bool = False) -> ScanResult:
    combined = ScanResult()
    objects = context.selected_objects if selected_only else context.scene.objects
    for obj in objects:
        partial = scan_object(obj)
        combined.hits.extend(partial.hits)
    return combined
