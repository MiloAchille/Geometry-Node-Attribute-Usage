import bpy
from . import operators
from . import scanner


# ---------------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------------

_MODE_ICON = {
    "READ":  "HIDE_OFF",
    "WRITE": "GREASEPENCIL",
    "BOTH":  "FILE_REFRESH",
}
_MODE_LABEL = {
    "READ":  "Read only",
    "WRITE": "Write only",
    "BOTH":  "Read & Write",
}


def _mode_key(modes: set[str]) -> str:
    if "READ" in modes and "WRITE" in modes:
        return "BOTH"
    return "WRITE" if "WRITE" in modes else "READ"


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def _apply_sort(names: list[str], result: scanner.ScanResult, sort_mode: str) -> list[str]:
    if sort_mode == "COUNT_DESC":
        return sorted(names, key=lambda n: result.count_for(n), reverse=True)
    if sort_mode == "COUNT_ASC":
        return sorted(names, key=lambda n: result.count_for(n))
    if sort_mode == "NAME_AZ":
        return sorted(names)
    if sort_mode == "NAME_ZA":
        return sorted(names, reverse=True)
    if sort_mode == "MODE":
        order = {"BOTH": 0, "WRITE": 1, "READ": 2}
        return sorted(names, key=lambda n: order[_mode_key(result.modes_for(n))])
    return names


# ---------------------------------------------------------------------------
# Results drawing
# ---------------------------------------------------------------------------

def _draw_results(layout: bpy.types.UILayout, result: scanner.ScanResult, wm) -> None:
    filter_text = wm.gnau_filter_text
    sort_mode   = wm.gnau_sort_mode
    show_mode   = wm.gnau_filter_mode   # "ALL" | "READ" | "WRITE" | "BOTH"

    names = result.unique_names()

    # Mode filter
    if show_mode != "ALL":
        names = [n for n in names if _mode_key(result.modes_for(n)) == show_mode]

    # Text filter
    if filter_text:
        names = [n for n in names if filter_text.lower() in n.lower()]

    # Sort
    names = _apply_sort(names, result, sort_mode)

    if not names:
        layout.label(text="No results match the current filter.", icon="INFO")
        return

    total_shown = sum(result.count_for(n) for n in names)
    layout.label(
        text=f"{len(names)} attributes  ·  {total_shown} nodes",
        icon="OUTLINER_DATA_FONT",
    )
    layout.separator(factor=0.3)

    for attr_name in names:
        hits  = result.hits_for(attr_name)
        modes = result.modes_for(attr_name)
        mkey  = _mode_key(modes)
        count = len(hits)

        box = layout.box()
        col = box.column(align=True)

        # Header row: mode icon · name · count · mode label
        header = col.row(align=True)
        header.alert = (mkey == "WRITE")
        header.label(text="", icon=_MODE_ICON[mkey])
        header.label(text=f'"{attr_name}"')
        right = header.row()
        right.alignment = "RIGHT"
        right.label(text=f"× {count}  {_MODE_LABEL[mkey]}")

        # Per-hit rows: object › modifier › tree › node [focus button]
        obj_map: dict = {}
        for h in hits:
            obj_map \
                .setdefault(h.object_name, {}) \
                .setdefault(h.modifier_name, {}) \
                .setdefault(h.node_tree_name, []) \
                .append(h)

        for obj_name, mods in obj_map.items():
            for mod_name, trees in mods.items():
                r = col.row()
                r.scale_y = 0.75
                r.label(text=f"  {obj_name}  ›  {mod_name}", icon="MODIFIER")

                for tree_name, tree_hits in trees.items():
                    tr = col.row()
                    tr.scale_y = 0.7
                    tr.label(text=f"    {tree_name}", icon="NODETREE")

                    for h in tree_hits:
                        nr = col.row(align=True)
                        nr.scale_y = 0.75

                        icon = "HIDE_OFF" if h.mode == "READ" else "GREASEPENCIL"
                        nr.label(
                            text=f"      {h.node_name}",
                            icon=icon,
                        )

                        # Focus button
                        op = nr.operator(
                            "gnau.focus_node",
                            text="",
                            icon="ZOOM_SELECTED",
                            emboss=True,
                        )
                        op.object_name    = h.object_name
                        op.modifier_name  = h.modifier_name
                        op.node_tree_name = h.node_tree_name
                        op.node_name      = h.node_name


# ---------------------------------------------------------------------------
# N-Panel: Main
# ---------------------------------------------------------------------------

class GNAU_PT_NodeEditor_Main(bpy.types.Panel):
    bl_label = "Attribute Usage"
    bl_idname = "GNAU_PT_node_editor_main"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Attr Usage"

    @classmethod
    def poll(cls, context):
        return context.space_data.type == "NODE_EDITOR"

    def draw(self, context):
        layout = self.layout
        wm     = context.window_manager
        result = operators.get_last_result()

        # ── Scan buttons ─────────────────────────────────────────────
        box = layout.box()
        box.label(text="Scan", icon="VIEWZOOM")
        row = box.row(align=True)
        op  = row.operator("gnau.scan_strings", text="Scene",    icon="SCENE_DATA")
        op.scope = "SCENE"
        op2 = row.operator("gnau.scan_strings", text="Selected", icon="RESTRICT_SELECT_OFF")
        op2.scope = "SELECTED"
        op3 = row.operator("gnau.scan_strings", text="Active",   icon="OBJECT_DATA")
        op3.scope = "ACTIVE"
        if result is not None:
            box.operator("gnau.clear_results", text="Clear Results", icon="X")

        node_box = layout.box()
        node_box.label(text="Selected Node String", icon="NODE")
        node_box.operator("gnau.get_selected_node_string", text="Get String", icon="EYEDROPPER")
        if wm.gnau_selected_node_socket:
            node_box.label(text=f"Socket: {wm.gnau_selected_node_socket}", icon="RIGHTARROW")
        if wm.gnau_selected_node_string:
            node_box.label(text=f"Value: {wm.gnau_selected_node_string}", icon="EVENT_A")
            node_box.operator(
                "gnau.use_selected_string_as_filter",
                text="Use This String As Search",
                icon="VIEWZOOM",
            )
        elif wm.gnau_selected_node_socket:
            node_box.label(text="Value: <unresolved or empty>", icon="ERROR")

        if result is None:
            layout.separator(factor=0.5)
            layout.label(text="Press a scan button above.", icon="INFO")
            return

        layout.separator(factor=0.5)

        # ── Filter & Sort ─────────────────────────────────────────────
        filter_box = layout.box()
        filter_box.label(text="Filter & Sort", icon="FILTER")

        search_row = filter_box.row(align=True)
        search_row.prop(wm, "gnau_filter_text", text="", icon="VIEWZOOM")
        search_row.operator("gnau.clear_search_filter", text="", icon="X")

        row = filter_box.row(align=True)
        row.label(text="Mode:")
        row.prop(wm, "gnau_filter_mode", expand=True)

        row2 = filter_box.row(align=True)
        row2.label(text="Sort:")
        row2.prop(wm, "gnau_sort_mode", text="")

        layout.separator(factor=0.3)

        # ── Legend ───────────────────────────────────────────────────
        legend = layout.row(align=True)
        legend.label(text="", icon="HIDE_OFF")
        legend.label(text="Read")
        legend.label(text="", icon="GREASEPENCIL")
        legend.label(text="Write")
        legend.label(text="", icon="FILE_REFRESH")
        legend.label(text="Both")
        layout.separator(factor=0.3)

        # ── Results ──────────────────────────────────────────────────
        _draw_results(layout, result, wm)


# ---------------------------------------------------------------------------
# N-Panel: Statistics
# ---------------------------------------------------------------------------

class GNAU_PT_NodeEditor_Stats(bpy.types.Panel):
    bl_label = "Statistics"
    bl_idname = "GNAU_PT_node_editor_stats"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Attr Usage"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.space_data.type == "NODE_EDITOR"

    def draw(self, context):
        layout = self.layout
        result = operators.get_last_result()
        if result is None:
            layout.label(text="No scan data.", icon="INFO")
            return

        names      = result.unique_names()
        total      = len(result.hits)
        reads      = [h for h in result.hits if h.mode == "READ"]
        writes     = [h for h in result.hits if h.mode == "WRITE"]
        read_only  = [n for n in names if result.modes_for(n) == {"READ"}]
        write_only = [n for n in names if result.modes_for(n) == {"WRITE"}]
        both       = [n for n in names if len(result.modes_for(n)) == 2]
        objects    = {h.object_name for h in result.hits}
        modifiers  = {(h.object_name, h.modifier_name) for h in result.hits}
        trees      = {h.node_tree_name for h in result.hits}

        col = layout.column(align=True)
        col.label(text=f"Unique attributes:   {len(names)}",   icon="COPY_ID")
        col.label(text=f"Total node hits:     {total}",        icon="OUTLINER_DATA_FONT")
        col.separator()
        col.label(text=f"Read nodes:          {len(reads)}",   icon="HIDE_OFF")
        col.label(text=f"Write nodes:         {len(writes)}",  icon="GREASEPENCIL")
        col.separator()
        col.label(text=f"Read-only attrs:     {len(read_only)}", icon="HIDE_OFF")
        col.label(text=f"Write-only attrs:    {len(write_only)}", icon="GREASEPENCIL")
        col.label(text=f"Read+Write attrs:    {len(both)}",    icon="FILE_REFRESH")
        col.separator()
        col.label(text=f"Objects scanned:     {len(objects)}", icon="OBJECT_DATA")
        col.label(text=f"Modifiers scanned:   {len(modifiers)}", icon="MODIFIER")
        col.label(text=f"Node trees visited:  {len(trees)}",   icon="NODETREE")

        if names:
            top = result.sorted_names()[0]
            col.separator()
            col.label(text=f'Most used: "{top}" (× {result.count_for(top)})', icon="SORT_DESC")


# ---------------------------------------------------------------------------
# WindowManager properties
# ---------------------------------------------------------------------------

def _register_props():
    bpy.types.WindowManager.gnau_filter_text = bpy.props.StringProperty(
        name="Search",
        description="Filter attributes by name",
        default="",
    )
    bpy.types.WindowManager.gnau_filter_mode = bpy.props.EnumProperty(
        name="Mode Filter",
        description="Show only attributes of a certain access mode",
        items=[
            ("ALL",   "All",   "Show all attributes",              "THREE_DOTS",   0),
            ("READ",  "Read",  "Only read (Named Attribute)",      "HIDE_OFF",     1),
            ("WRITE", "Write", "Only write (Store Named Attr.)",   "GREASEPENCIL", 2),
            ("BOTH",  "Both",  "Only attrs used for read & write", "FILE_REFRESH", 3),
        ],
        default="ALL",
    )
    bpy.types.WindowManager.gnau_sort_mode = bpy.props.EnumProperty(
        name="Sort By",
        description="How to order the attribute list",
        items=[
            ("COUNT_DESC", "Count ↓ (most used first)",   "Most used at the top"),
            ("COUNT_ASC",  "Count ↑ (least used first)",  "Least used at the top"),
            ("NAME_AZ",    "Name A → Z",                  "Alphabetical ascending"),
            ("NAME_ZA",    "Name Z → A",                  "Alphabetical descending"),
            ("MODE",       "Mode (Both, Write, Read)",    "Group by access mode"),
        ],
        default="COUNT_DESC",
    )
    bpy.types.WindowManager.gnau_selected_node_string = bpy.props.StringProperty(
        name="Selected Node String",
        description="Last resolved string from the selected node",
        default="",
    )
    bpy.types.WindowManager.gnau_selected_node_socket = bpy.props.StringProperty(
        name="Selected Node Socket",
        description="Socket used for the last selected node string resolution",
        default="",
    )


def _unregister_props():
    del bpy.types.WindowManager.gnau_filter_text
    del bpy.types.WindowManager.gnau_filter_mode
    del bpy.types.WindowManager.gnau_sort_mode
    del bpy.types.WindowManager.gnau_selected_node_string
    del bpy.types.WindowManager.gnau_selected_node_socket


classes = (
    GNAU_PT_NodeEditor_Main,
    GNAU_PT_NodeEditor_Stats,
)


def register():
    _register_props()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _unregister_props()
