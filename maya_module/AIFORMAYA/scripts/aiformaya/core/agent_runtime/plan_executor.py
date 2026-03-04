# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import logging
import maya.utils as maya_utils

from ...tools.registry import call_tool

try:
    from ..memory import EntityMemory
except ImportError:
    class EntityMemory(object):
        @classmethod
        def update_last_action(cls, name): pass

from . import task_analyzer
from . import task_planner

# Global cancellation flag
EXECUTION_CANCELLED = False

def cancel_execution():
    global EXECUTION_CANCELLED
    EXECUTION_CANCELLED = True

# Friendly names for UX
_FRIENDLY_NAMES = {
    "maya.create_cube": u"创建方块",
    "maya.create_sphere": u"创建球体",
    "maya.create_cylinder": u"创建圆柱",
    "maya.create_plane": u"创建平面",
    "maya.create_camera": u"创建摄像机",
    "maya.create_three_point_lighting": u"创建三点布光",
    "maya.create_turntable": u"创建自动转台",
    "maya.randomize_transforms": u"随机摆放位置",
    "maya.camera_look_at": u"摄像机看向目标",
    "maya.camera_frame_selection": u"摄像机框选(F)",
    "maya.group_and_center": u"打组并居中轴心",
    "maya.execute_python_code": u"批量创建 / 执行代码",
    "maya.rename_batch": u"批量重命名",
    "maya.assign_color_materials": u"分配随机彩色材质",
    "maya.create_loop_rotate": u"创建循环旋转动画",
    "maya.create_bounce_animation": u"创建弹跳动画"
}

log = logging.getLogger("aiformaya")

def execute_plan(plan, available_tools, emit_status=None):
    """
    Executes a generated JSON plan sequentially in Maya.
    
    plan: dict like {"steps": [{"tool": "maya.xxx", "args": {...}}, ...]}
    available_tools: list of tool dicts from _TOOLS_SCHEMA_CACHE
    emit_status: callback(str) for real-time UI logging
    
    Returns: JSON response suitable for yielding back to the UI chat history.
    """
    steps = plan.get("steps", [])[:5]
    if not steps:
        if emit_status: emit_status(u"⚠️ 任务规划未生成有效步骤")
        return u"未生成执行步骤。"
        
    allowed = {t["name"] for t in available_tools}
    results_summary = []
    
    global EXECUTION_CANCELLED
    EXECUTION_CANCELLED = False
    
    for i, step in enumerate(steps):
        if EXECUTION_CANCELLED:
            if emit_status: emit_status(u"⛔ 任务已取消")
            results_summary.append(u"⛔ 任务已被用户手动取消")
            break
            
        tool = step.get("tool")
        args = step.get("args") or {}
        raw_name = tool.replace("maya.", "") if tool else "Unknown"
        friendly_name = _FRIENDLY_NAMES.get(tool, raw_name)
        
        log.info(u"Executor step %d/%d: %s, args=%s", i+1, len(steps), tool, args)
        
        if tool not in allowed:
            msg = u"受限步骤，工具不在授权列表中: %s" % tool
            log.error(msg)
            if emit_status: emit_status(u"❌ " + msg)
            results_summary.append(u"- [%d] %s: ❌ 错误 (%s)" % (i+1, friendly_name, msg))
            break
            
        # Security: protect execute_python_code against hallucinated external unallowed scripts
        if tool == "maya.execute_python_code":
            code = args.get("code", "")
            if "cmds." not in code and "mel." not in code:
                msg = u"受限步骤，Python 代码不包含 cmds 调用"
                log.error(msg)
                if emit_status: emit_status(u"❌ " + msg)
                results_summary.append(u"❌ 错误: %s" % msg)
                break
            
            # Additional blacklist check
            forbidden_modules = ["import os", "import sys", "subprocess", "open(", "__import__", "eval(", "exec("]
            if any(f in code for f in forbidden_modules):
                msg = u"受限步骤，Python 代码包含被禁用的系统危险模块"
                log.error(msg)
                if emit_status: emit_status(u"❌ " + msg)
                results_summary.append(u"❌ 错误: %s" % msg)
                break
            
        if emit_status:
            emit_status(u"⚙️ AI 执行步骤 %d/%d: %s" % (i+1, len(steps), friendly_name))
            
        def _do():
            # Real tool call in Main Thread
            return call_tool(tool, args)
            
        try:
            tool_result = maya_utils.executeInMainThreadWithResult(_do)
        except Exception as e:
            msg = u"执行步骤 %d (%s) 时崩溃: %s" % (i+1, tool, e)
            log.error(msg)
            if emit_status: emit_status(u"❌ " + msg)
            results_summary.append(u"- [%d] %s: ❌ 失败 (%s)" % (i+1, friendly_name, e))
            break # Halt execution on error
            
        ok = bool(tool_result.get("ok"))
        if not ok:
            err = tool_result.get("error", {})
            err_msg = err.get("message", "未知错误")
            msg = u"步骤 %d 失败: %s" % (i+1, err_msg)
            log.error(msg)
            if emit_status: emit_status(u"❌ " + msg)
            results_summary.append(u"❌ %s: 错误 (%s)" % (friendly_name, err_msg))
            break
            
        # Success
        results_summary.append(u"✅ 已%s" % friendly_name)
        log.info(u"Step result OK: %s", tool_result)
        
        # Memory Updates
        try:
            EntityMemory.update_last_action(tool)
            # Try to infer memory from tool name or execute_python_code output if any
            if "create" in tool and isinstance(tool_result, dict):
                res = tool_result.get("result") or tool_result
                sel = res.get("selection") or res.get("created") or res.get("transform") or res.get("camera") or res.get("group") or res.get("locator")
                if sel:
                    if not isinstance(sel, list):
                        sel = [sel]
                    entity_type = tool.replace("maya.create_", "")
                    EntityMemory.update_last_created(entity_type, sel)
                    EntityMemory.update_last_selected(sel)
            elif tool == "maya.execute_python_code":
                sel = maya_utils.executeInMainThreadWithResult(lambda: __import__('maya.cmds').cmds.ls(sl=True))
                if sel:
                    EntityMemory.update_last_selected(sel)
        except Exception:
            pass
            
    if emit_status:
        emit_status(u"✅ 计划已执行完毕")
        
    return u"**批量任务执行报告：**\n" + "\n".join(results_summary)
