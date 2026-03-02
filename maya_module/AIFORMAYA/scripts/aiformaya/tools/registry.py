# -*- coding: utf-8 -*-
from __future__ import absolute_import

def _maya_tools():
    # 延迟导入，避免 Maya 启动时的循环依赖问题
    from aiformaya.tools import maya_tools
    return maya_tools


def tools_schema():
    return _maya_tools().tools_schema()


def call_tool(name, arguments):
    n = (name or "").strip()
    alias_map = {
        "maya.create_polygon_cube": "maya.create_cube",
        "maya.create_poly_cube": "maya.create_cube",
        "maya.make_cube": "maya.create_cube",
        "maya.create_cube_polygon": "maya.create_cube",
        "maya.create_box": "maya.create_cube",
        "maya.create_polygon_sphere": "maya.create_sphere",
        "maya.make_sphere": "maya.create_sphere",
        "maya.sphere": "maya.create_sphere",
        "maya.create_polygon_cylinder": "maya.create_cylinder",
        "maya.create_cylinder_polygon": "maya.create_cylinder",
        "maya.cylinder": "maya.create_cylinder",
        "maya.create_polygon_plane": "maya.create_plane",
        "maya.create_plane_polygon": "maya.create_plane",
        "maya.move": "maya.set_translate",
        "maya.translate": "maya.set_translate",
        "maya.set_translation": "maya.set_translate",
        "maya.create_and_animate": "maya.create_and_animate_translate_x",
        "maya.animate_translate_x": "maya.create_and_animate_translate_x",
        "maya.retime_animation": "maya.retime_keys",
        "maya.move_key": "maya.retime_keys",
        "maya.move_keys": "maya.retime_keys",
        "maya.retime_animation_range": "maya.retime_range",
        "maya.scale_keys": "maya.retime_range",
        "maya.list_scene": "maya.list_animated_nodes",
        "maya.list_scene_summary": "maya.list_animated_nodes",
        "maya.create_bounce_ball": "maya.create_bouncing_ball",
        "maya.bounce_ball": "maya.create_bouncing_ball",
        "maya.create_bouncing_ball": "maya.create_bouncing_ball",
        "maya.camera": "maya.create_camera",
    }
    canon = alias_map.get(n, n)
    return _maya_tools().call_tool(canon, arguments or {})
