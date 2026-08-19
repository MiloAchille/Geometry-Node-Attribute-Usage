bl_info = {
    "name": "Geometry Node Attribute Usage",
    "author": "Custom",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "Properties > Object > Geometry Node Attribute Usage",
    "description": "Scans geometry node modifiers and reports all string/attribute usages with frequency analysis",
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
