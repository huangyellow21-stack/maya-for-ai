# -*- coding: utf-8 -*-
"""
AIFORMAYA v2.0 — Tool Registry with Tool Guard
Adds: parameter validation, batch limits, auto-retry, debug logging
"""
from __future__ import absolute_import
import logging
import os

log = logging.getLogger("aiformaya")

# Batch operation limits
_BATCH_LIMITS = {
    "maya.rename_batch": ("objects", 300),
    "maya.randomize_transforms": ("objects", 200),
    "maya.assign_color_materials": ("objects", 200),
}


def _maya_tools():
    from aiformaya.tools import maya_tools
    return maya_tools


def tools_schema():
    return _maya_tools().tools_schema()


_ALIAS_MAP = {
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
    "maya.camera": "maya.create_camera",
}


def _validate_args(canon, arguments):
    """
    Lightweight parameter guard.
    Returns (ok: bool, error_msg: str or None)
    """
    # Batch limits
    if canon in _BATCH_LIMITS:
        param, limit = _BATCH_LIMITS[canon]
        val = arguments.get(param)
        if isinstance(val, list) and len(val) > limit:
            return False, u"%s: \u5bf9\u8c61\u6570\u91cf\u8d85\u8fc7\u9650\u5236 %d\uff08\u5f53\u524d %d\uff09" % (canon, limit, len(val))
    return True, None


def call_tool(name, arguments):
    n = (name or "").strip()
    canon = _ALIAS_MAP.get(n, n)

    # Guard: validate args
    ok_val, err_msg = _validate_args(canon, arguments or {})
    if not ok_val:
        log.warning("Tool guard rejected: %s — %s", canon, err_msg)
        return {"ok": False, "error": err_msg}

    log.debug("call_tool: %s args=%s", canon, str(arguments)[:200])

    # Execute with one auto-retry on failure
    result = None
    for attempt in range(2):
        try:
            result = _maya_tools().call_tool(canon, arguments or {})
            if result.get("ok") or attempt == 1:
                break
            log.warning("Tool %s failed (attempt %d), retrying...", canon, attempt + 1)
        except Exception as e:
            log.error("Tool %s exception (attempt %d): %s", canon, attempt + 1, e)
            result = {"ok": False, "error": str(e)}
            if attempt == 1:
                break

    log.debug("call_tool result: ok=%s | tool=%s", result.get("ok") if result else None, canon)
    return result or {"ok": False, "error": "unknown error"}
