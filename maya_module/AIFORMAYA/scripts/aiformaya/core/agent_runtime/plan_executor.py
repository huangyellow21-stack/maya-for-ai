# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import logging
import re

try:
    import maya.utils as maya_utils
except ImportError:
    maya_utils = None

from ...tools.registry import call_tool

try:
    from ..memory import EntityMemory
except ImportError:
    class EntityMemory(object):
        @classmethod
        def update_last_action(cls, name): pass
        @classmethod
        def update_last_created(cls, t, n): pass
        @classmethod
        def update_last_selected(cls, n): pass
        @classmethod
        def get_last_created(cls): return {}
        @classmethod
        def get_recent_objects(cls): return []

# Global cancellation flag
EXECUTION_CANCELLED = False

def cancel_execution():
    global EXECUTION_CANCELLED
    EXECUTION_CANCELLED = True

# Friendly names for UX
_FRIENDLY_NAMES = {
    "maya.create_cube": u"\u521b\u5efa\u65b9\u5757",
    "maya.create_sphere": u"\u521b\u5efa\u7403\u4f53",
    "maya.create_cylinder": u"\u521b\u5efa\u5706\u67f1",
    "maya.create_plane": u"\u521b\u5efa\u5e73\u9762",
    "maya.create_camera": u"\u521b\u5efa\u6444\u50cf\u673a",
    "maya.create_three_point_lighting": u"\u521b\u5efa\u4e09\u70b9\u5e03\u5149",
    "maya.create_turntable": u"\u521b\u5efa\u81ea\u52a8\u8f6c\u53f0",
    "maya.randomize_transforms": u"\u968f\u673a\u6446\u653e\u4f4d\u7f6e",
    "maya.camera_look_at": u"\u6444\u50cf\u673a\u770b\u5411\u76ee\u6807",
    "maya.camera_frame_selection": u"\u6444\u50cf\u673a\u6846\u9009(F)",
    "maya.group_and_center": u"\u6253\u7ec4\u5e76\u5c45\u4e2d\u8f74\u5fc3",
    "maya.execute_python_code": u"\u6279\u91cf\u521b\u5efa / \u6267\u884c\u4ee3\u7801",
    "maya.rename_batch": u"\u6279\u91cf\u91cd\u547d\u540d",
    "maya.assign_color_materials": u"\u5206\u914d\u968f\u673a\u5f69\u8272\u6750\u8d28",
    "maya.create_loop_rotate": u"\u521b\u5efa\u5faa\u73af\u65cb\u8f6c\u52a8\u753b",
    "maya.create_bounce_animation": u"\u521b\u5efa\u5f39\u8df3\u52a8\u753b",
}

log = logging.getLogger("aiformaya")

# ──────────────────────────────────────────────────────────────
# THREADING RULE:
#   run_chat() is called from a WORKER thread (dock.py).
#   All maya.cmds calls MUST be dispatched to the main thread
#   via ONE executeInMainThreadWithResult call.
#   NEVER nest a second executeInMainThreadWithResult inside.
#   call_tool() does NOT use executeInMainThread internally,
#   so wrapping it here is safe and sufficient.
# ──────────────────────────────────────────────────────────────

def _call_tool_on_main_thread(tool_name, tool_args):
    """
    Dispatch a single tool call to Maya's main thread.
    This is the ONLY executeInMainThreadWithResult in the entire executor.
    """
    def _do():
        return call_tool(tool_name, tool_args)

    if maya_utils:
        return maya_utils.executeInMainThreadWithResult(_do)
    else:
        return _do()


def execute_plan(plan, available_tools, emit_status=None):
    """
    Executes a generated JSON plan sequentially in Maya.

    plan: dict like {"steps": [{"tool": "maya.xxx", "args": {...}}, ...]}
    available_tools: list of tool dicts from _TOOLS_SCHEMA_CACHE
    emit_status: callback(str) for real-time UI logging

    Returns: string summary.
    """
    steps = plan.get("steps", [])[:8]
    if not steps:
        if emit_status:
            emit_status(u"\u26a0\ufe0f \u4efb\u52a1\u89c4\u5212\u672a\u751f\u6210\u6709\u6548\u6b65\u9aa4")
        return u"\u672a\u751f\u6210\u6267\u884c\u6b65\u9aa4\u3002"

    allowed = {t["name"] for t in available_tools}
    results_summary = []
    entity_vars = {}

    global EXECUTION_CANCELLED
    EXECUTION_CANCELLED = False

    for i, step in enumerate(steps):
        if EXECUTION_CANCELLED:
            if emit_status:
                emit_status(u"\u26d4 \u4efb\u52a1\u5df2\u53d6\u6d88")
            results_summary.append(u"\u26d4 \u4efb\u52a1\u5df2\u88ab\u7528\u6237\u624b\u52a8\u53d6\u6d88")
            break

        tool = step.get("tool")
        raw_args = step.get("args") or {}

        # --- Variable Substitution ---
        args = {}
        for k, v in raw_args.items():
            if isinstance(v, (str, bytes)):
                if v.startswith("{") and v.endswith("}"):
                    var_name = v[1:-1]
                    if var_name in entity_vars:
                        val = entity_vars[var_name]
                        if isinstance(val, list) and len(val) == 1:
                            args[k] = val[0]
                        else:
                            args[k] = val
                    else:
                        args[k] = v
                else:
                    args[k] = v
            else:
                args[k] = v

        # Support execute_python_code injecting `variables` into locals
        if tool == "maya.execute_python_code" and entity_vars:
            safe_vars_str = json.dumps(entity_vars)
            code = args.get("code", "")
            injected_code = "variables = %s\n%s" % (safe_vars_str, code)
            args["code"] = injected_code

        raw_name = tool.replace("maya.", "") if tool else "Unknown"
        friendly_name = _FRIENDLY_NAMES.get(tool, raw_name)

        log.info(u"Executor step %d/%d: %s, args=%s", i + 1, len(steps), tool, args)

        # --- Security: tool whitelist ---
        if tool not in allowed:
            msg = u"\u53d7\u9650\u6b65\u9aa4\uff0c\u5de5\u5177\u4e0d\u5728\u6388\u6743\u5217\u8868\u4e2d: %s" % tool
            log.error(msg)
            if emit_status:
                emit_status(u"\u274c " + msg)
            results_summary.append(u"- [%d] %s: \u274c \u9519\u8bef (%s)" % (i + 1, friendly_name, msg))
            break

        # --- Security: execute_python_code content checks ---
        if tool == "maya.execute_python_code":
            code = args.get("code", "")

            # Fix python3 f-strings for maya python2
            if 'f"' in code or "f'" in code:
                code = re.sub(r'f"(.*?)\{(.*?)\}(.*?)"', r'"\1%s\3" % (\2)', code)
                code = re.sub(r"f'(.*?)\{(.*?)\}(.*?)'", r"'\1%s\3' % (\2)", code)
                code = re.sub(r'%s:0*(\d+)d', r'%0\1d', code)
            args["code"] = code

            # Must contain cmds or mel
            if "cmds." not in code and "mel." not in code:
                msg = u"\u53d7\u9650\u6b65\u9aa4\uff0cPython \u4ee3\u7801\u4e0d\u5305\u542b cmds \u8c03\u7528"
                log.error(msg)
                if emit_status:
                    emit_status(u"\u274c " + msg)
                results_summary.append(u"\u274c \u9519\u8bef: %s" % msg)
                break

            # Block dangerous modules
            forbidden_modules = ["import os", "import sys", "subprocess", "open(", "__import__", "eval(", "exec("]
            if any(f in code for f in forbidden_modules):
                msg = u"\u53d7\u9650\u6b65\u9aa4\uff0cPython \u4ee3\u7801\u5305\u542b\u88ab\u7981\u7528\u7684\u7cfb\u7edf\u5371\u9669\u6a21\u5757"
                log.error(msg)
                if emit_status:
                    emit_status(u"\u274c " + msg)
                results_summary.append(u"\u274c \u9519\u8bef: %s" % msg)
                break

            # Block infinite loops
            if re.search(r'while\s+(True|1)\s*:', code):
                msg = u"\u53d7\u9650\u6b65\u9aa4\uff0cPython \u4ee3\u7801\u5305\u542b\u65e0\u9650\u5faa\u73af"
                log.error(msg)
                if emit_status:
                    emit_status(u"\u274c " + msg)
                results_summary.append(u"\u274c \u9519\u8bef: %s" % msg)
                break

            # Limit code length
            if len(code) > 3000:
                msg = u"\u53d7\u9650\u6b65\u9aa4\uff0cPython \u4ee3\u7801\u8d85\u8fc7 3000 \u5b57\u7b26\u9650\u5236"
                log.error(msg)
                if emit_status:
                    emit_status(u"\u274c " + msg)
                results_summary.append(u"\u274c \u9519\u8bef: %s" % msg)
                break

        if emit_status:
            emit_status(u"\u2699\ufe0f AI \u6267\u884c\u6b65\u9aa4 %d/%d: %s" % (i + 1, len(steps), friendly_name))

        # --- Execute on main thread (SINGLE dispatch, no nesting) ---
        try:
            tool_result = _call_tool_on_main_thread(tool, args)
        except Exception as e:
            msg = u"\u6267\u884c\u6b65\u9aa4 %d (%s) \u65f6\u5d29\u6e83: %s" % (i + 1, tool, e)
            log.error(msg)
            if emit_status:
                emit_status(u"\u274c " + msg)
            results_summary.append(u"- [%d] %s: \u274c \u5931\u8d25 (%s)" % (i + 1, friendly_name, e))
            break

        ok = bool(tool_result.get("ok"))
        if not ok:
            err = tool_result.get("error", {})
            if isinstance(err, dict):
                err_msg = err.get("message", u"\u672a\u77e5\u9519\u8bef")
            else:
                err_msg = str(err)
            msg = u"\u6b65\u9aa4 %d \u5931\u8d25: %s" % (i + 1, err_msg)
            log.error(msg)
            if emit_status:
                emit_status(u"\u274c " + msg)
            results_summary.append(u"\u274c %s: \u9519\u8bef (%s)" % (friendly_name, err_msg))
            break

        # Success
        results_summary.append(u"\u2705 \u5df2%s" % friendly_name)
        log.info(u"Step result OK: %s", tool_result)

        # --- Value Saving (save_as) ---
        save_as_key = step.get("save_as")
        if save_as_key and isinstance(tool_result, dict):
            res = tool_result.get("result") or tool_result
            sel = (res.get("selection") or res.get("created") or
                   res.get("transform") or res.get("camera") or
                   res.get("group") or res.get("locator"))
            if sel:
                if not isinstance(sel, list):
                    sel = [sel]
                entity_vars[save_as_key] = sel

        # --- Memory Updates ---
        # NOTE: No executeInMainThreadWithResult here!
        # We get creation info from the tool_result dict directly.
        try:
            EntityMemory.update_last_action(tool)
            if "create" in tool and isinstance(tool_result, dict):
                res = tool_result.get("result") or tool_result
                sel = (res.get("selection") or res.get("created") or
                       res.get("transform") or res.get("camera") or
                       res.get("group") or res.get("locator"))
                if sel:
                    if not isinstance(sel, list):
                        sel = [sel]
                    entity_type = tool.replace("maya.create_", "")
                    EntityMemory.update_last_created(entity_type, sel)
                    EntityMemory.update_last_selected(sel)
        except Exception:
            pass

    if emit_status:
        emit_status(u"\u2705 \u8ba1\u5212\u5df2\u6267\u884c\u5b8c\u6bd5")

    return u"**\u6267\u884c\u62a5\u544a\uff1a**\n" + "\n".join(results_summary)
