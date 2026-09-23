bl_info = {
    "name": "Geometry Node Attribute Usage",
    "author": "Baptiste Mollicone",
    "version": (1, 1, 1),
    "blender": (5, 2, 0),
    "location": "Properties > Object > Geometry Node Attribute Usage",
    "description": "Scans geometry node modifiers for named attribute READ/WRITE usage, with inclusive mode filters and tree-order sorting (first write / first read)",
    "category": "Object",
}

import bpy

from . import scanner
from . import ui
from . import operators


def register():
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()


if __name__ == "__main__":
    register()
