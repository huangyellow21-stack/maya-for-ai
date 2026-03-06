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
    "maya.create_cube":                   u"创建方块",
    "maya.create_sphere":                 u"创建球体",
    "maya.create_cylinder":               u"创建圆柱",
    "maya.create_plane":                  u"创建平面",
    "maya.create_camera":                 u"创建摄像机",
    "maya.create_three_point_lighting":   u"创建三点布光",
    "maya.create_turntable":              u"创建自动转台",
    "maya.randomize_transforms":          u"随机摆放位置",
    "maya.camera_look_at":                u"摄像机看向目标",
    "maya.camera_frame_selection":        u"摄像机框选(F)",
    "maya.group_and_center":              u"打组并居中轴心",
    "maya.execute_python_code":           u"批量创建 / 执行代码",
    "maya.rename_batch":                  u"批量重命名",
    "maya.assign_color_materials":        u"分配随机彩色材质",
    "maya.create_loop_rotate":            u"创建循环旋转动画",
    "maya.create_bounce_animation":       u"创建弹跳动画",
    "maya.create_bouncing_ball":          u"创建弹跳球",
    "maya.create_and_animate_translate_x": u"创建X轴平移动画",
    # FX 特效 — 一定要有可读 UI 名，不能显示原始 tool 名
    "maya.import_bomb_asset":             u"导入爆炸特效模板",
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


def _resolve_alias(tool_name):
    """Resolve tool aliases to canonical names. Lazy import to avoid circular deps."""
    try:
        from ..agent import _ALIAS_MAP
        return _ALIAS_MAP.get(tool_name, tool_name)
    except ImportError:
        return tool_name

def execute_plan(plan, available_tools, emit_status=None):
    """
    Executes a generated JSON plan sequentially in Maya.

    plan: dict like {"steps": [{"tool": "maya.xxx", "args": {...}}, ...]}
    available_tools: list of tool dicts from _TOOLS_SCHEMA_CACHE
    emit_status: callback(str) for real-time UI logging

    Returns: dict with keys:
        text_summary   — human-readable string (backward compat)
        step_results   — list of {tool, args, ok, result, error, purpose}
        created_nodes  — list of node names created during execution
        planned_tools  — tools listed in the plan
        actual_tools   — tools actually executed (success only)
        extra_tools    — executed but not in plan (should be empty)
        missing_tools  — in plan but not executed
    """
    steps = plan.get("steps", [])[:8]
    if not steps:
        if emit_status:
            emit_status(u"\u26a0\ufe0f \u4efb\u52a1\u89c4\u5212\u672a\u751f\u6210\u6709\u6548\u6b65\u9aa4")
        return {
            "text_summary": u"\u672a\u751f\u6210\u6267\u884c\u6b65\u9aa4\u3002",
            "step_results": [],
            "created_nodes": [],
            "planned_tools": [],
            "actual_tools": [],
            "extra_tools": [],
            "missing_tools": [],
        }

    allowed = {t["name"] for t in available_tools}
    results_summary = []
    entity_vars = {}
    step_results = []
    created_nodes = []
    actual_tools_executed = []
    planned_tools = [_resolve_alias(s.get("tool", "")) for s in steps]

    global EXECUTION_CANCELLED
    EXECUTION_CANCELLED = False

    for i, step in enumerate(steps):
        if EXECUTION_CANCELLED:
            if emit_status:
                emit_status(u"\u26d4 \u4efb\u52a1\u5df2\u53d6\u6d88")
            results_summary.append(u"\u26d4 \u4efb\u52a1\u5df2\u88ab\u7528\u6237\u624b\u52a8\u53d6\u6d88")
            break

        tool = step.get("tool")
        tool = _resolve_alias(tool)
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
        purpose = step.get("purpose", friendly_name)

        log.info(u"Executor step %d/%d: %s, args=%s", i + 1, len(steps), tool, args)

        if emit_status:
            emit_status(u"\u2699\ufe0f AI \u6267\u884c\u6b65\u9aa4 %d/%d: %s" % (i + 1, len(steps), purpose))

        # --- Security: tool whitelist ---
        if tool not in allowed:
            msg = u"\u53d7\u9650\u6b65\u9aa4\uff0c\u5de5\u5177\u4e0d\u5728\u6388\u6743\u5217\u8868\u4e2d: %s" % tool
            log.error(msg)
            if emit_status:
                emit_status(u"\u274c " + msg)
            results_summary.append(u"- [%d] %s: \u274c \u9519\u8bef (%s)" % (i + 1, friendly_name, msg))
            step_results.append({"tool": tool, "args": args, "ok": False, "error": msg, "purpose": purpose})
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
                step_results.append({"tool": tool, "args": args, "ok": False, "error": msg, "purpose": purpose})
                break

            # Block dangerous modules
            forbidden_modules = ["import os", "import sys", "subprocess", "open(", "__import__", "eval(", "exec("]
            if any(f in code for f in forbidden_modules):
                msg = u"\u53d7\u9650\u6b65\u9aa4\uff0cPython \u4ee3\u7801\u5305\u542b\u88ab\u7981\u7528\u7684\u7cfb\u7edf\u5371\u9669\u6a21\u5757"
                log.error(msg)
                if emit_status:
                    emit_status(u"\u274c " + msg)
                results_summary.append(u"\u274c \u9519\u8bef: %s" % msg)
                step_results.append({"tool": tool, "args": args, "ok": False, "error": msg, "purpose": purpose})
                break

            # Block infinite loops
            if re.search(r'while\s+(True|1)\s*:', code):
                msg = u"\u53d7\u9650\u6b65\u9aa4\uff0cPython \u4ee3\u7801\u5305\u542b\u65e0\u9650\u5faa\u73af"
                log.error(msg)
                if emit_status:
                    emit_status(u"\u274c " + msg)
                results_summary.append(u"\u274c \u9519\u8bef: %s" % msg)
                step_results.append({"tool": tool, "args": args, "ok": False, "error": msg, "purpose": purpose})
                break

            # Limit code length
            if len(code) > 3000:
                msg = u"\u53d7\u9650\u6b65\u9aa4\uff0cPython \u4ee3\u7801\u8d85\u8fc7 3000 \u5b57\u7b26\u9650\u5236"
                log.error(msg)
                if emit_status:
                    emit_status(u"\u274c " + msg)
                results_summary.append(u"\u274c \u9519\u8bef: %s" % msg)
                step_results.append({"tool": tool, "args": args, "ok": False, "error": msg, "purpose": purpose})
                break

        # --- Execute on main thread (SINGLE dispatch, no nesting) ---
        try:
            tool_result = _call_tool_on_main_thread(tool, args)
        except Exception as e:
            msg = u"\u6267\u884c\u6b65\u9aa4 %d (%s) \u65f6\u5d29\u6e83: %s" % (i + 1, tool, e)
            log.error(msg)
            if emit_status:
                emit_status(u"\u274c " + msg)
            results_summary.append(u"- [%d] %s: \u274c \u5931\u8d25 (%s)" % (i + 1, friendly_name, e))
            step_results.append({"tool": tool, "args": args, "ok": False, "error": str(e), "purpose": purpose})
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
            step_results.append({"tool": tool, "args": args, "ok": False, "error": err_msg, "purpose": purpose, "result": None})
            break

        # Success — record step result
        result_data = tool_result.get("result") or {}
        step_results.append({"tool": tool, "args": args, "ok": True, "error": None, "purpose": purpose, "result": result_data})
        actual_tools_executed.append(tool)
        results_summary.append(u"\u2705 \u5df2%s" % friendly_name)
        log.info(u"Step result OK: %s", tool_result)

        # Collect created node names for diagnostics (dedup during collection)
        if isinstance(result_data, dict):
            for key in ("created", "name", "transform", "camera", "node", "selection"):
                val = result_data.get(key)
                if val:
                    if isinstance(val, list):
                        for v in val:
                            s = str(v)
                            if s not in created_nodes:
                                created_nodes.append(s)
                    else:
                        s = str(val)
                        if s not in created_nodes:
                            created_nodes.append(s)

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

    # ── Plan vs Actual comparison log ──
    actual_set = set(actual_tools_executed)
    planned_set = set(planned_tools)
    extra_tools = sorted(actual_set - planned_set)
    missing_tools = sorted(planned_set - actual_set)

    log.info(u"[PLAN VS ACTUAL] planned=%s", planned_tools)
    log.info(u"[PLAN VS ACTUAL] actual=%s", actual_tools_executed)
    if extra_tools:
        log.warning(u"[PLAN VS ACTUAL] EXTRA tools (not in plan): %s", extra_tools)
    if missing_tools:
        log.warning(u"[PLAN VS ACTUAL] MISSING tools (in plan but not run): %s", missing_tools)
    log.info(u"[CREATED NODES] %s", created_nodes)

    text_summary = u"**\u6267\u884c\u62a5\u544a\uff1a**\n" + "\n".join(results_summary)

    return {
        "text_summary": text_summary,
        "step_results": step_results,
        "created_nodes": list(set(created_nodes)),
        "planned_tools": planned_tools,
        "actual_tools": actual_tools_executed,
        "extra_tools": extra_tools,
        "missing_tools": missing_tools,
    }

