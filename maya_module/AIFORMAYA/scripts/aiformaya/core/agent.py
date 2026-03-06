# -*- coding: utf-8 -*-
"""
AIFORMAYA v2.0  —  Multi-Agent Router + Scene Awareness
"""
from __future__ import absolute_import
import re
import json as _json_module
import threading
import traceback
import uuid
import json
import logging
import os

import maya.utils as maya_utils

from .http_client import post_json, HttpError
from . import config as cfgmod
from .agent_runtime.task_analyzer import analyze_task
from .agent_runtime.task_planner import plan_task
from .agent_runtime.plan_executor import execute_plan
from .agent_runtime.plan_cache import get_cached_plan, save_plan
from .agent_runtime.plan_validator import validate_plan
from .agent_runtime.intent_parser import parse_intent
from .agent_runtime.scene_context import resolve_scene_context
from .agent_runtime.capability_planner import plan_capabilities
from .agent_runtime.capability_resolver import resolve_capabilities
from .agent_runtime.smart_planner import build_planning_prompt, parse_plan_response, validate_smart_plan, summarize_plan_for_ui
from .agent_runtime.semantic_objects import resolve_semantic_objects
from .agent_runtime.task_graph import build_task_graph
from .agent_runtime.plan_generator import generate_plan
from ..tools.maya_tools import TOOLS as _TOOLS_SCHEMA_CACHE_RAW, tools_schema
from ..tools.maya_tools import ConfirmationError
from ..tools.registry import call_tool
try:
    from .memory import EntityMemory
except ImportError:
    class EntityMemory(object):
        @classmethod
        def get_summary(cls): return ""
        @classmethod
        def update_last_created(cls, t, n): pass
        @classmethod
        def update_last_selected(cls, n): pass
        @classmethod
        def update_last_camera(cls, n): pass
        @classmethod
        def update_recent_objects(cls, names): pass
        @classmethod
        def get_last_created(cls): return {}
        @classmethod
        def get_recent_objects(cls): return []

# ─────────────────────────────────────────────
# Debug logger
# ─────────────────────────────────────────────
_LOG_FILE = os.path.join(os.path.expanduser("~"), "aiformaya_debug.log")
logging.basicConfig(
    filename=_LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("aiformaya")


class AgentError(Exception):
    pass


# ─────────────────────────────────────────────
# Execution Policy
# ─────────────────────────────────────────────
# 创建类 + 查询类：直接执行，不询问
AUTO_EXECUTE_TOOLS = {
    "maya.create_cube",
    "maya.create_sphere",
    "maya.create_cylinder",
    "maya.create_plane",
    "maya.create_camera",
    "maya.create_turntable",
    "maya.create_three_point_lighting",
    "maya.create_bouncing_ball",
    "maya.create_loop_rotate",
    "maya.create_ping_pong_translate",
    "maya.create_and_animate_translate_x",
    "maya.camera_look_at",
    "maya.camera_frame_selection",
    "maya.duplicate_objects",
    "maya.freeze_transforms",
    "maya.center_pivot",
    "maya.scan_scene_summary",
    "maya.list_selection",
    "maya.list_cameras",
    "maya.list_animated_nodes",
    "maya.list_tools",
}

def should_auto_execute(tool_name):
    """Return True if this tool (already canonicalized) should execute without confirmation."""
    return tool_name in AUTO_EXECUTE_TOOLS


# ─────────────────────────────────────────────
# Alias map
# ─────────────────────────────────────────────
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
    "maya.retime_animation_range": "maya.retime_range",
    "maya.scale_keys": "maya.retime_range",
    "maya.create_bounce_ball": "maya.create_bouncing_ball",
    "maya.bounce_ball": "maya.create_bouncing_ball",
    "maya.camera": "maya.create_camera",
    # v2.1: camera aim aliases
    "maya.aim_camera_at_selection": "maya.camera_look_at",
    "maya.look_at": "maya.camera_look_at",
    "maya.camera_aim": "maya.camera_look_at",
    "maya.frame_selection": "maya.camera_frame_selection",
    "maya.fit_camera": "maya.camera_frame_selection",
    "maya.view_fit": "maya.camera_frame_selection",
    # v2.1: transform aliases
    "maya.freeze": "maya.freeze_transforms",
    "maya.make_identity": "maya.freeze_transforms",
    "maya.center_origin": "maya.center_pivot",
    "maya.pivot_center": "maya.center_pivot",
    "maya.duplicate": "maya.duplicate_objects",
    "maya.delete": "maya.delete_selected",
    "maya.parent": "maya.parent_objects",
}

_SINGLE_SHOT_TOOLS = {
    "maya.create_and_animate_translate_x",
    "maya.retime_keys",
    "maya.retime_range",
    "maya.create_bouncing_ball",
    "maya.create_loop_rotate",
    "maya.create_ping_pong_translate",
    "maya.import_bomb_asset",
    "maya.create_three_point_lighting",
    "maya.create_turntable",
    "maya.cleanup_scene",
    "maya.group_and_center",
    "maya.randomize_transforms",
    "maya.assign_color_materials",
}

# ─────────────────────────────────────────────
# Tool subsets per agent
# ─────────────────────────────────────────────
_AGENT_TOOLS = {
    "modeling": {
        "maya.create_cube", "maya.create_sphere", "maya.create_cylinder",
        "maya.create_plane", "maya.rename_batch", "maya.group_and_center",
        "maya.create_camera", "maya.camera_look_at", "maya.aim_at_target",
        "maya.camera_frame_selection",
        "maya.create_three_point_lighting", "maya.create_turntable",
        "maya.ask_user_confirmation",
    },
    "animation": {
        "maya.set_key", "maya.shift_keys", "maya.retime_keys",
        "maya.retime_range", "maya.create_loop_rotate",
        "maya.create_ping_pong_translate", "maya.create_bouncing_ball",
        "maya.create_and_animate_translate_x",
        "maya.list_animated_nodes", "maya.list_selection",
        "maya.ask_user_confirmation",
    },
    "lighting": {
        "maya.create_three_point_lighting", "maya.create_turntable",
        "maya.ask_user_confirmation",
    },
    "scene": {
        "maya.scan_scene_summary", "maya.list_selection",
        "maya.list_cameras", "maya.list_animated_nodes", "maya.list_tools",
    },
    "general": {
        "maya.cleanup_scene", "maya.execute_python_code",
        "maya.ask_user_confirmation", "maya.list_tools",
        "maya.scan_scene_summary", "maya.list_selection",
    },
}

def _filter_tools_for_agent(agent_type):
    allowed = _AGENT_TOOLS.get(agent_type, set())
    # Use cached schema to avoid rebuilding every call
    return [t for t in _TOOLS_SCHEMA_CACHE if t.get("name") in allowed]

# Cache tools schema at import time — rebuilt only when Maya reloads the module
_TOOLS_SCHEMA_CACHE = tools_schema()


# ─────────────────────────────────────────────
# Router Agent — keyword matching, no LLM call
# ─────────────────────────────────────────────
_ROUTER_RULES = [
    ("scene",     [u"场景", u"有什么", u"分析", u"摘要", u"查询", u"选中了什么",
                   u"里面有", u"包含什么", u"摄像机列表"]),
    ("animation", [u"动画", u"旋转", u"位移", u"关键帧", u"弹跳", u"循环",
                   u"k帧", u"K帧", u"retime", "animate", "keyframe"]),
    ("modeling", [u"创建", u"建模", u"物体", u"几何", u"材质", u"球", u"方块",
                  u"立方", u"平面", u"柱", u"组", u"复制", u"冻结", u"轴心", u"删除", "delete",
                  u"生成", u"做一个", u"放一个", u"来个", u"摄像机", u"看向", u"框选",
                  "create", "make", "add", "spawn", "model", "duplicate", "freeze", "camera", "look_at", "frame"]),
    ("lighting",  [u"灯光", u"布光", u"三点光", u"转台", u"渲染",
                   "light", "turntable"]),
    ("general",   [u"脚本", u"代码", u"工具", u"插件", "python", "script"]),
]

def router_agent(user_text):
    """Route user request to the most relevant sub-agent type."""
    agent_type = "general"
    text_lower = user_text.lower()
    for agent, keywords in _ROUTER_RULES:
        for kw in keywords:
            # Use lower() only for ASCII keywords to avoid mangling Chinese
            matched = (kw.lower() in text_lower) if all(ord(c) < 128 for c in kw) else (kw in user_text)
            if matched:
                agent_type = agent
                break
        if agent_type != "general":
            break
    log.info(u"Router result: %s | text: %.60s", agent_type, user_text)
    return agent_type


# ─────────────────────────────────────────────
# Scene Context
# ─────────────────────────────────────────────
def _get_scene_context_safe():
    """
    Attempt to get a lightweight scene snapshot for context injection.
    Must be called from Maya main thread. Returns '' on any failure.
    """
    try:
        from ..tools.registry import call_tool as _ct
        result = _ct("maya.scan_scene_summary", {})
        if result.get("ok"):
            data = result.get("result") or {}
            lines = [u"\u300a\u5f53\u524d\u573a\u666f\u5feb\u7167\u300b"]  # 《当前场景快照》
            geo = data.get("geometry", [])
            if geo:
                lines.append(u"\u51e0\u4f55\u4f53: " + u", ".join(str(g.get("name","?")) for g in geo[:8]))
            cams = data.get("user_cameras") or data.get("cameras") or []
            if cams:
                lines.append(u"\u6444\u50cf\u673a: " + u", ".join(str(c) for c in cams[:4]))
            lights = data.get("lights", [])
            if lights:
                lines.append(u"\u706f\u5149: " + u", ".join(str(l) for l in lights[:4]))
            sel = data.get("selection", [])
            if sel:
                lines.append(u"\u5f53\u524d\u9009\u4e2d: " + u", ".join(str(s) for s in sel[:4]))
            # v2.2: total object count for scene-scale awareness
            total = data.get("total_user_objects")
            if total:
                lines.append(u"\u5bf9\u8c61\u603b\u6570: %s" % total)
            return u"\n".join(lines)
    except Exception as e:
        log.debug("get_scene_context failed: %s", e)
    return ""


# ─────────────────────────────────────────────
# System prompts per agent
# ─────────────────────────────────────────────
_EXEC_POLICY = u"""

## 工具使用优先级（必须遵守）

### Level 1 优先：专用 Maya 工具
如果存在与用户请求直接对应的工具，必须调用该工具。
不要用 execute_python_code 替代已有工具。

示例：
- 创建一个摄像机 → maya.create_camera
- 创建一个球 → maya.create_sphere
- 复制选中物体 → maya.duplicate_objects
- 创建三点布光 → maya.create_three_point_lighting
- 创建转台摄像机 → maya.create_turntable
- 删除选中物体 → maya.delete_selected
- 冻结变换 → maya.freeze_transforms

### Level 4 最低优先： execute_python_code
只有以下情况才允许使用 execute_python_code：
- 需要循环创建大量对象（如：创建10个球）
- 需要复杂数学逻辑
- 需要多个 Maya API 组合
- 没有对应的专用工具

### 严格禁止
禁止用 execute_python_code 做：
- 创建基础几何体（球/方块/柱/平面）
- 创建摄像机或灯光
- 复制 / 删除 / 冻结 / 居中轴心
- 设置关键帧

## 执行策略
《创建类》：直接调用对应专用工具，完成后告知结果并给出一条智能建议。
《查询类》：直接执行，然后用自然语言回答。
《修改已有元素》：先说明方案，再问用户：“需要我帮你执行吗？”
《破坏性操作》：必须调用 maya.ask_user_confirmation 加以确认。

禁止：不要输出 JSON 卡片或 [ACTION_PLAN]；回复用中文，自然流畅。
"""

# ─────────────────────────────────────────────
# Intent Resolver — pronoun → entity name
# ─────────────────────────────────────────────
_PRONOUNS_ZH = [u"\u5b83", u"\u5b83\u4eec", u"\u8fd9\u4e2a", u"\u8fd9\u4e9b",
                u"\u90a3\u4e2a", u"\u90a3\u4e9b", u"\u6b64\u7269\u4f53"]  # 它/它们/这个/这些/那个/那些/此物体
_PRONOUNS_EN = ["it", "them", "this", "these", "that", "those", "selected"]

def resolve_entities(user_text):
    """
    Lightweight intent resolver: replace pronouns with the concrete entity name
    from EntityMemory so the LLM doesn't have to guess.
    Returns the enriched text (may be unchanged if no memory or no pronouns found).
    """
    try:
        mem = EntityMemory.load()
        # Best candidate: last selected > last created object > last camera
        last_sel = mem.get("last_selected", "")
        last_cam = mem.get("last_camera", "")
        last_created = ""
        lc = mem.get("last_created", {})
        if lc:
            # prefer most recently created regardless of type
            last_created = list(lc.values())[-1] if lc else ""

        target = last_sel or last_created or last_cam
        if not target:
            return user_text  # nothing in memory, can't resolve

        resolved = user_text
        for pron in _PRONOUNS_ZH:
            if pron in resolved:
                resolved = resolved.replace(pron, u"%s\uff08%s\uff09" % (pron, target))
                break  # replace once, keep natural phrasing
        for pron in _PRONOUNS_EN:
            import re as _re
            # whole-word match only
            resolved = _re.sub(r"\b" + pron + r"\b",
                               "%s(%s)" % (pron, target), resolved,
                               count=1, flags=_re.IGNORECASE)

        if resolved != user_text:
            log.info(u"IntentResolver: '%s' -> '%s'", user_text[:60], resolved[:60])
        return resolved
    except Exception as e:
        log.debug("resolve_entities failed: %s", e)
        return user_text


# ─────────────────────────────────────────────
# Tool Capability Prompt builder
# ─────────────────────────────────────────────
def _build_tool_capability_line(tool_list):
    """
    Return a compact one-line capability statement for system prompt.
    Tells LLM explicitly what tools it has — more reliable than schema alone.
    """
    if not tool_list:
        return u""
    names = [t.get("name", "").replace("maya.", "") for t in tool_list]
    return u"\n\n## \u53ef\u7528\u5de5\u5177\n" + u"\u3001".join(names)  # 可用工具\ncreate_cube、create_sphere...


def _base_prompt(role_desc, scene_ctx, mem_summary, tool_list=None):
    prompt = role_desc + _EXEC_POLICY
    if tool_list:
        prompt += _build_tool_capability_line(tool_list)
    if scene_ctx:
        prompt += u"\n" + scene_ctx
    if mem_summary:
        prompt += u"\n\n" + mem_summary
    return prompt

_NARRATION_RULES = """
## 回复规则

当工具执行完毕后，你需要用自然语言总结结果。遵循以下原则：

1. **绝不暴露**工具名、函数名、JSON、变量名
   - ✗ "已调用 maya.create_bouncing_ball"
   - ✓ "已经创建了一个弹跳小球"

2. **说人话**：像一个有经验的 Maya 同事在跟你说话
   - ✗ "执行报告：✅ 已create_bouncing_ball"  
   - ✓ "小球已经放好了，弹跳3次，高度会逐渐衰减"

3. **提炼关键参数**，不要罗列全部
   - 只说用户关心的：尺寸、帧范围、数量等
   - 忽略内部细节：subdiv、constraint名称等

4. **给后续建议**（1-2个就够）
   - 有动画 → 提醒播放预览
   - 有摄像机 → 提醒切换视角
   - 参数可调 → 提醒可以修改

5. **长度控制**：3-5行，不要写小作文

6. **如果执行失败**，用通俗语言解释原因和建议
"""

def _build_modeling_messages(user_text, scene_ctx, mem_summary, tool_list=None):
    role = (
        u"\u4f60\u662f\u4e00\u4e2a Maya 2020 \u5efa\u6a21\u4e13\u5bb6\u548c\u573a\u666f\u7ed3\u6784\u5e08\u3002"
        u"\u5c13\u957f\u5904\u7406\uff1a\u521b\u5efa\u51e0\u4f55\u4f53\u3001\u573a\u666f\u7ef4\u62a4\u3001\u6279\u91cf\u547d\u540d\u3001\u65b0\u589e\u6750\u8d28\u3002"
        u"\u521b\u5efa\u5b8c\u6210\u540e\u52a1\u5fc5\u7ed9\u51fa\u4e00\u6761\u4e13\u4e1a\u5efa\u8bae\u3002"
        + _NARRATION_RULES
    )
    return [{"role": "system", "content": _base_prompt(role, scene_ctx, mem_summary, tool_list)},
            {"role": "user", "content": user_text}]

def _build_animation_messages(user_text, scene_ctx, mem_summary, tool_list=None):
    role = (
        u"\u4f60\u662f\u4e00\u4e2a Maya 2020 \u52a8\u753b TD\u3002\n"
        u"\u5c13\u957f\u521b\u5efa\u5404\u7c7b\u52a8\u753b\uff1a\u5e73\u79fb\u3001\u65cb\u8f6c\u3001\u5f39\u8df3\u3001\u5faa\u73af\u3001\u53d8\u5f62\u5173\u952e\u5e27\u3002\n"
        u"\u521b\u5efa\u52a8\u753b\u540e\u52a1\u5fc5\u544a\u77e5\u5173\u952e\u5e27\u8303\u56f4\u5e76\u5efa\u8bae\u662f\u5426\u9700\u8981\u66f2\u7ebf\u8c03\u6574\u3002\n"
        + _NARRATION_RULES
    )
    return [{"role": "system", "content": _base_prompt(role, scene_ctx, mem_summary, tool_list)},
            {"role": "user", "content": user_text}]

def _build_lighting_messages(user_text, scene_ctx, mem_summary, tool_list=None):
    role = (
        u"\u4f60\u662f\u4e00\u4e2a Maya 2020 \u706f\u5149\u5e08\u548c\u6444\u50cf\u5e08\u3002\n"
        u"\u5c13\u957f\u521b\u5efa\u4e09\u70b9\u706f\u5149\u3001\u8bbe\u7f6e\u4e3b\u6458\u50cf\u673a\u3001\u8f6c\u53f0\u5c55\u793a\u3002\n"
        u"\u521b\u5efa\u540e\u52a1\u5fc5\u8bc4\u4ef7\u5149\u6e90\u5e76\u5efa\u8bae\u6e32\u67d3\u8bbe\u7f6e\u3002\n"
        + _NARRATION_RULES
    )
    return [{"role": "system", "content": _base_prompt(role, scene_ctx, mem_summary, tool_list)},
            {"role": "user", "content": user_text}]

def _build_scene_messages(user_text, scene_ctx, mem_summary, tool_list=None):
    role = (
        u"\u4f60\u662f\u4e00\u4e2a Maya 2020 \u573a\u666f\u5206\u6790\u5e08\u3002\n"
        u"\u4f18\u5148\u8c03\u7528 maya.scan_scene_summary \u83b7\u53d6\u5b8c\u6574\u573a\u666f\u4fe1\u606f\uff0c\u518d\u7ec4\u7ec7\u81ea\u7136\u8bed\u8a00\u56de\u7b54\u3002\n"
        u"\u4e0d\u8981\u5217\u51fa Maya \u5185\u7f6e\u76f8\u673a\uff08persp/top/front/side\uff09\u3002\n"
        + _NARRATION_RULES
    )
    return [{"role": "system", "content": _base_prompt(role, scene_ctx, mem_summary, tool_list)},
            {"role": "user", "content": user_text}]

def _build_general_messages(user_text, scene_ctx, mem_summary, tool_list=None):
    role = (
        u"\u4f60\u662f\u4e00\u4e2a Maya 2020 \u8d44\u6df1\u6280\u672f\u603b\u76d1\uff08TD\uff09\u3002\n"
        u"\u5904\u7406\u590d\u6742\u4efb\u52a1\u3001\u9ad8\u7ea7\u64cd\u4f5c\u3001Python \u811a\u672c\u3001\u8de8\u88c1\u9886\u57df\u95ee\u9898\u3002\n"
        u"\u65e0\u5bf9\u5e94\u4e13\u7528\u5de5\u5177\u7684\u9700\u6c42\u5fc5\u987b\u7528 maya.execute_python_code\u3002\n"
        + _NARRATION_RULES
    )
    return [{"role": "system", "content": _base_prompt(role, scene_ctx, mem_summary, tool_list)},
            {"role": "user", "content": user_text}]

_AGENT_BUILDERS = {
    "modeling":  _build_modeling_messages,
    "animation": _build_animation_messages,
    "lighting":  _build_lighting_messages,
    "scene":     _build_scene_messages,
    "general":   _build_general_messages,
}

# Smart suggestions after creation tools
_CREATION_SUGGESTIONS = {
    "maya.create_sphere":   u"\U0001f4a1 建议：可以为它创建旋转动画，或添加反射材质。需要我继续吗？",
    "maya.create_cube":     u"\U0001f4a1 建议：可以对它进行随机变换，或打组整理场景。需要我继续吗？",
    "maya.create_camera":   u"\U0001f4a1 建议：可以为摄像机创建转台动画或设置视角。需要我继续吗？",
    "maya.create_turntable":u"\U0001f4a1 建议：转台已就绪，可以进行渲染或调整速度。需要我帮你吗？",
    "maya.create_three_point_lighting": u"\U0001f4a1 建议：三点灯已建立，可以调整各灯亮度或渲染测试。需要我继续吗？",
    "maya.create_bouncing_ball": u"\U0001f4a1 建议：弹跳球动画已创建，可以调整缓动曲线或添加阴影。",
}

def _get_suggestion(canon_name):
    return _CREATION_SUGGESTIONS.get(canon_name, "")


def narrate_execution_result(user_text, result_summary):
    """
    将技术执行摘要（result_summary）转成自然语言，返回字符串。
    若 narration LLM 调用失败，返回原始 result_summary（安全 fallback）。
    可被 dock.py 直接 import 使用。
    """
    try:
        cfg = cfgmod.load_config()
        gateway_url = (cfg.get("gateway_url") or "").rstrip("/")
        if not gateway_url:
            return result_summary
        provider = (cfg.get("provider") or "deepseek").strip().lower()
        api_key  = cfg.get("gemini_api_key") if provider == "gemini" else cfg.get("deepseek_api_key")
        model    = _model_for_provider(cfg)
        narration_messages = _build_narration_messages(user_text, result_summary)
        payload = {
            "provider":          provider,
            "api_key":           api_key,
            "model":             model,
            "messages":          narration_messages,
            "temperature":       float(cfg.get("temperature", 0.2)),
            "max_output_tokens": 1024,
        }
        resp = post_json(gateway_url + "/chat", payload, timeout_s=30)
        if resp:
            return (
                resp.get("content")
                or resp.get("message", {}).get("content", result_summary)
                or result_summary
            )
    except Exception as e:
        log.warning(u"narrate_execution_result failed: %s — returning raw summary", e)
    return result_summary


def _build_plan_confirm_payload(user_text, plan):
    """将 raw smart plan 转成 plan_confirm reply 结构，供前端渲染计划卡使用。"""
    import uuid as _uuid
    ui_plan = summarize_plan_for_ui(plan, user_text)
    ui_plan["plan_id"] = str(_uuid.uuid4())   # 唯一 ID，用于追踪计划与执行的对应关系
    ui_plan["raw_plan"] = plan                 # 保留完整 raw plan，执行时直接用
    ui_plan["original_user_text"] = user_text
    return {
        "type": "plan_confirm",
        "content": u"我已生成执行计划，请确认是否执行。",
        "plan": ui_plan,
    }



def _build_narration_messages(user_text, result_summary, history_messages=None):
    """
    Constructs a request to the LLM to narrate the successfully completed tool execution results.
    """
    messages = history_messages[:] if history_messages else []
    
    role = (
        u"\u4f60\u662f\u4e00\u4e2a\u7ecf\u9a8c\u4e30\u5bcc\u7684 Maya \u540c\u4e8b\u3002"
        u"\u4f60\u521a\u521a\u5e2e\u52a9\u7528\u6237\u5b8c\u6210\u4e86\u4ee5\u4e0b\u64cd\u4f5c\u3002\u8bf7\u6839\u636e\u6267\u884c\u7ed3\u679c\u8fdb\u884c\u81ea\u7136\u8bed\u8a00\u603b\u7ed3\u3002\n"
        + _NARRATION_RULES
    )
    
    # Prepend or adapt the system prompt
    if messages and messages[0].get("role") == "system":
        messages[0] = {"role": "system", "content": role}
    else:
        messages.insert(0, {"role": "system", "content": role})
        
    prompt = (
        u"\u3010\u7528\u6237\u539f\u59cb\u8bf7\u6c42\u3011\n%s\n\n"
        u"\u3010\u6267\u884c\u7ed3\u679c\u3011\n%s\n\n"
        u"\u8bf7\u6839\u636e\u4ee5\u4e0a\u6267\u884c\u7ed3\u679c\uff0c\u7528\u81ea\u7136\u7684\u4e2d\u6587\u56de\u590d\u7528\u6237\u3002"
    ) % (user_text, result_summary)
    
    messages.append({"role": "user", "content": prompt})
    return messages


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────
def _model_for_provider(cfg):
    provider = (cfg.get("provider") or "deepseek").strip().lower()
    if provider == "gemini":
        return cfg.get("model_gemini") or "gemini-1.5-flash"
    return cfg.get("model_deepseek") or "deepseek-chat"

def _clean_history(msgs):
    out = []
    for m in msgs:
        content = m.get("content", "")
        if m.get("role") == "user" and content.strip().startswith("[TOOL_RESULT]"):
            continue
        if m.get("role") == "assistant" and '"type": "tool_call"' in content:
            continue
        out.append(m)
    return out

def _update_memory_from_result(canon_name, tool_result):
    """Auto-update Entity Memory after a successful tool execution."""
    if not tool_result.get("ok"):
        return
    result = tool_result.get("result") or {}
    try:
        # Extract created object name
        created = (result.get("created") or result.get("name") or
                   result.get("node") or result.get("camera") or "")
        if created:
            # Determine type
            if "sphere" in canon_name:
                EntityMemory.update_last_created("sphere", created)
            elif "cube" in canon_name:
                EntityMemory.update_last_created("cube", created)
            elif "camera" in canon_name:
                EntityMemory.update_last_created("camera", created)
                EntityMemory.update_last_camera(created)
            elif "light" in canon_name or "lighting" in canon_name:
                EntityMemory.update_last_created("light", created)
            else:
                EntityMemory.update_last_created("object", created)
            EntityMemory.update_recent_objects([created])
        # Selection update
        sel = result.get("selected") or result.get("nodes") or []
        if sel:
            EntityMemory.update_last_selected(sel[0])
            EntityMemory.update_recent_objects(sel[:5])
    except Exception as e:
        log.debug("memory update failed: %s", e)


# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────
def run_chat(user_text, history_messages=None, max_turns=8, on_status=None):
    """
    Returns: (reply_dict, new_history_messages)
    reply_dict types: text | confirm | exec_result

    on_status: optional callable(str) for real-time status updates
               called with '\u6b63\u5728\u601d\u8003...', '\u6b63\u5728\u6267\u884c...', etc.
    """
    def _emit(msg):
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    cfg = cfgmod.load_config()
    gateway_url = (cfg.get("gateway_url") or "").rstrip("/")
    if not gateway_url:
        raise AgentError(u"\u7f3a\u5c11 gateway_url")

    provider = (cfg.get("provider") or "deepseek").strip().lower()
    api_key = cfg.get("gemini_api_key") if provider == "gemini" else cfg.get("deepseek_api_key")
    model = _model_for_provider(cfg)
    temperature = float(cfg.get("temperature", 0.2))

    effective_text = user_text
    # v2.2 Intent Resolver: expand pronouns before routing and LLM call
    effective_text = resolve_entities(effective_text)

    # ── Route to sub-agent ──
    agent_type = router_agent(effective_text)
    log.info(u"Agent type: %s | text: %s", agent_type, effective_text[:60])

    # ── Handle simple Execute confirmation ──
    if effective_text.strip() in [u"执行", u"是的", u"好", u"确认", u"开始"]:
        last_turn = history_messages[-1] if history_messages else {}
        if last_turn.get("role") == "assistant":
            ast_text = last_turn.get("content", "")
            # Look for python code block
            match = re.search(r'```python(.*?)```', ast_text, re.DOTALL)
            if match:
                code_str = match.group(1).strip()
                _emit(u"AI 触发代码直接执行...")
                ad_hoc_plan = {
                    "steps": [
                        {
                            "tool": "maya.execute_python_code",
                            "args": {"code": code_str}
                        }
                    ]
                }
                try:
                    res_summary = execute_plan(ad_hoc_plan, available_tools=_TOOLS_SCHEMA_CACHE, emit_status=_emit)
                    messages = history_messages if history_messages else []
                    messages.append({"role": "user", "content": effective_text})
                    messages.append({"role": "assistant", "content": res_summary})
                    return {"type": "text", "content": res_summary}, _clean_history(messages)
                except Exception as e:
                    error_msg = u"执行失败：%s" % e
                    _emit(u"❌ " + error_msg)
                    messages = history_messages if history_messages else []
                    messages.append({"role": "user", "content": effective_text})
                    messages.append({"role": "assistant", "content": error_msg})
                    return {"type": "text", "content": error_msg}, _clean_history(messages)

    # ── Scene context (lightweight, may fail silently) ──
    try:
        scene_ctx = maya_utils.executeInMainThreadWithResult(_get_scene_context_safe)
    except Exception:
        scene_ctx = ""

    # ── Memory summary ──
    mem_summary = EntityMemory.get_summary()

    # ── Tool subset ──
    tools = _filter_tools_for_agent(agent_type)

    # v2.3 Agent Runtime: Branch for complex tasks
    _emit(u"AI 分析请求...")
    task_type = analyze_task(effective_text)
    log.info(u"Task type: %s | text: %.60s", task_type, effective_text)
    
    if task_type == "COMPLEX_TASK":
        tools = _TOOLS_SCHEMA_CACHE

        log.info(u"Complex task detected, using Smart Planner (LLM-based)...")
        _emit(u"AI 正在深度分析任务...")

        # ── 尝试生成并验证计划 ──
        plan = None
        try:
            # 1. 构造规划 Prompt
            planning_system, planning_user = build_planning_prompt(
                effective_text,
                scene_context=scene_ctx,
            )
            planning_messages = [
                {"role": "system", "content": planning_system},
                {"role": "user",   "content": planning_user},
            ]

            _emit(u"AI 正在进行空间推理与任务规划...")

            planning_payload = {
                "provider":          provider,
                "api_key":           api_key,
                "model":             model,
                "messages":          planning_messages,
                "temperature":       0.1,
                "max_output_tokens": 4096,
            }

            plan_resp = post_json(gateway_url + "/chat", planning_payload, timeout_s=60)

            if plan_resp:
                plan_text = (
                    plan_resp.get("content")
                    or plan_resp.get("message", {}).get("content", "")
                )
            else:
                plan_text = ""

            log.info(u"Smart Planner raw response:\n%s", plan_text[:2000])

            # 2. 解析计划
            plan = parse_plan_response(plan_text)
            if not plan:
                log.warning(u"Smart plan parse failed — no valid JSON plan returned by LLM")

        except Exception as e:
            log.error(u"Smart Planner exception: %s", e)
            traceback.print_exc()
            plan = None

        # 3. 验证计划（parse 成功才走验证）
        if plan:
            validation_errors = validate_smart_plan(plan, _TOOLS_SCHEMA_CACHE)
            # 过滤掉信息性标记（如 __has_python_code__），只保留真正的错误
            real_errors = [e for e in validation_errors if not e.startswith("__")]
            if real_errors:
                log.warning(u"Smart plan validation errors: %s", real_errors)
                plan = None

        # 4a. 规划成功 → 返回 plan_confirm，等待用户确认（不自动执行）
        if plan:
            reasoning = plan.get("reasoning", "")
            if reasoning:
                log.info(u"Smart Planner reasoning: %s", reasoning)

            _emit(u"计划已生成，等待确认...")
            payload = _build_plan_confirm_payload(effective_text, plan)
            messages = history_messages[:] if history_messages else []
            messages.append({"role": "user",      "content": effective_text})
            messages.append({"role": "assistant",  "content": payload["content"]})
            return payload, _clean_history(messages)

        # 4b. 规划失败 → 返回友好提示，严禁 fall-through 到标准 LLM 路径
        else:
            fail_msg = (
                u"这个任务比较复杂，我暂时没能生成稳定的执行计划。"
                u"建议你换一种更明确的描述，或者拆成更小的步骤来做。"
            )
            log.warning(u"COMPLEX_TASK planning failed — returning failure message, NOT falling through.")
            _emit(u"⚠️ 规划失败")
            messages = history_messages[:] if history_messages else []
            messages.append({"role": "user",      "content": effective_text})
            messages.append({"role": "assistant",  "content": fail_msg})
            return {"type": "text", "content": fail_msg}, _clean_history(messages)
        # ↑ COMPLEX_TASK 所有路径均已 return，不会 fall-through
            
    if task_type == "SIMPLE_TOOL":
        log.info(u"Evaluating Deterministic NL Pipeline...")
        try:
            # 1. Deterministic Intent Parse
            intent = parse_intent(effective_text)
            
            # We assume intent is valid if it found actionable verbs
            if intent.get("actions"):
                _emit(u"AI 分析意图并规划系统能力...")
                caps = plan_capabilities(intent)
                
                # Semantic Objects
                semantic = resolve_semantic_objects(intent)
                
                # Enforce execution ordering
                graph = build_task_graph(caps)
                
                # Context (already handles its own main thread dispatch safely via _query_scene)
                try:
                    ctx = resolve_scene_context(intent.get("targets", []), effective_text)
                except Exception:
                    ctx = {"selection": [], "last_created": {}, "target_nodes": []}
                
                # Map tools
                resolved, unsupported = resolve_capabilities(graph, intent.get("targets", []), _TOOLS_SCHEMA_CACHE)
                
                if unsupported:
                    # Unsupported fast-fail loop
                    msg = "\n".join(unsupported)
                    _emit(u"❌ " + msg)
                    messages = history_messages if history_messages else []
                    messages.append({"role": "user", "content": effective_text})
                    messages.append({"role": "assistant", "content": msg})
                    return {"type": "text", "content": msg}, _clean_history(messages)
                    
                if resolved:
                    _emit(u"AI (Stable Layer) 生成执行计划...")
                    import json
                    
                    # 1. Cache Check for Deterministic Plan
                    plan = get_cached_plan(effective_text, intent=intent)
                    
                    if not plan:
                        plan = generate_plan(intent, resolved, ctx, semantic)
                        save_plan(effective_text, plan, intent=intent)
                        
                    log.info(u"Deterministic Plan: %s", json.dumps(plan, ensure_ascii=False))
                    
                    # Execute Deterministic Plan
                    result_summary = execute_plan(plan, available_tools=_TOOLS_SCHEMA_CACHE, emit_status=_emit)
                    
                    _emit(u"AI 生成自然语言回复...")
                    narration_messages = _build_narration_messages(effective_text, result_summary)
                    
                    payload = {
                        "provider": provider,
                        "api_key": api_key,
                        "model": model,
                        "messages": narration_messages,
                        "temperature": temperature,
                        "max_output_tokens": 1024,
                    }
                    try:
                        resp = post_json(gateway_url + "/chat", payload, timeout_s=30)
                        if resp:
                            final_text = (
                                resp.get("content")
                                or resp.get("message", {}).get("content", result_summary)
                            )
                        else:
                            final_text = result_summary
                    except Exception as e:
                        log.error("Narration failed: %s", e)
                        final_text = result_summary
                        
                    messages = history_messages if history_messages else []
                    messages.append({"role": "user", "content": effective_text})
                    messages.append({"role": "assistant", "content": final_text})
                    return {"type": "text", "content": final_text}, _clean_history(messages)

            # If deterministic pipeline had no resolved tools or no actions,
            # fall through cleanly to the standard LLM tool-calling path below.
            # Do NOT create a second LLM planning path here — it causes
            # Deterministic → LLM → Executor cascading conflicts.
            log.info(u"Deterministic pipeline had no resolved tools. Falling through to standard LLM path.")
        except Exception as e:
            log.error(u"Agent Runtime \u5f02\u5e38: %s", e)
            import traceback
            traceback.print_exc()
            # Fall through to standard LLM path instead of returning an error
            log.info(u"Deterministic pipeline error, falling through to LLM path.")

    # ── Build messages (pass tool_list for capability prompt) ──
    builder = _AGENT_BUILDERS.get(agent_type, _build_general_messages)
    messages = []
    if history_messages:
        messages.extend(history_messages)
        fresh_sys = builder(effective_text, scene_ctx, mem_summary, tool_list=tools)[0]
        if messages and messages[0].get("role") == "system":
            messages[0] = fresh_sys
        else:
            messages.insert(0, fresh_sys)
        messages.append({"role": "user", "content": effective_text})
    else:
        messages.extend(builder(effective_text, scene_ctx, mem_summary, tool_list=tools))

    turns = 0
    executed = set()
    _first_llm_call = [True]  # track first vs subsequent calls
    consecutive_tool_calls = 0

    while True:
        turns += 1
        if turns > max_turns:
            raise AgentError(u"tool-call \u56de\u5408\u8fc7\u591a\uff0c\u5df2\u505c\u6b62\uff08%d\uff09" % max_turns)

        payload = {
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "max_output_tokens": 4096,
        }

        # Status: thinking
        if _first_llm_call[0]:
            _emit(u"AI 思考中...")
            _first_llm_call[0] = False
        else:
            _emit(u"AI 继续思考...")

        try:
            resp = post_json(gateway_url + "/chat", payload, timeout_s=60)
        except HttpError as e:
            error_msg = u"\u7f51\u5173\u8bf7\u6c42\u8d85\u65f6\u6216\u5931\u8d25\uff1a%s" % str(e)
            _emit(error_msg)
            messages.append({"role": "assistant", "content": error_msg})
            return {"type": "text", "content": error_msg}, _clean_history(messages)

        if not resp or "type" not in resp:
            raise AgentError(u"\u7f51\u5173\u8fd4\u56de\u65e0\u6548\u54cd\u5e94\uff1a%s" % str(resp))

        # ── Text response ──
        if resp["type"] == "message":
            consecutive_tool_calls = 0 # reset limit
            text = resp.get("content") or u"\u64cd\u4f5c\u5df2\u5b8c\u6210\u3002"
            text = re.sub(r"^(\u6267\u884c\u56de\u636e\uff1a|\u6267\u884c\u56de\u636e:|<final_execution_receipt>)", "", text.strip()).strip()
            if not text:
                text = u"\u64cd\u4f5c\u5df2\u5b8c\u6210\u3002"
            log.debug("LLM text reply: %s", text[:80])
            messages.append({"role": "assistant", "content": text})
            return {"type": "text", "content": text}, _clean_history(messages)

        # ── Tool call ──
        if resp["type"] == "tool_call":
            consecutive_tool_calls += 1
            if consecutive_tool_calls >= 3:
                raise AgentError(u"连续调用工具次数过多(>3次)，已强制终止以防死循环。")
            tool_name = resp.get("name")
            args = resp.get("arguments") or {}
            content = resp.get("content") or ""
            canon_name = _ALIAS_MAP.get(tool_name, tool_name)

            # v2.2 logging
            if canon_name != tool_name:
                log.info(u"Alias mapped: %s -> %s", tool_name, canon_name)
            log.info(u"Tool call: %s", tool_name)
            log.debug(u"Tool args: %s", args)

            # v2.2 create-guard: intercept before should_auto_execute
            _CREATE_KEYWORDS = [u"\u521b\u5efa", u"\u751f\u6210", u"\u505a\u4e00\u4e2a", u"\u6765\u4e2a",
                                "create", "make", "add", "new", "spawn", "camera", "sphere", "cube"]
            if canon_name == "maya.execute_python_code":
                if any(kw in effective_text.lower() or kw in effective_text for kw in _CREATE_KEYWORDS):
                    log.info(u"create-guard: blocked execute_python_code for create request")
                    messages.append({"role": "user", "content":
                        u"[SYSTEM GUARD] \u4f60\u8bd5\u56fe\u7528 execute_python_code \u521b\u5efa\u5bf9\u8c61\u3002"
                        u"\u8bf7\u6539\u7528\u5bf9\u5e94\u7684\u4e13\u7528\u5de5\u5177\uff1a"
                        u"create_sphere / create_cube / create_camera / create_cylinder \u7b49\u3002"
                        u"\u7981\u6b62\u7528 execute_python_code \u521b\u5efa\u5355\u4e2a\u5bf9\u8c61\u3002"})
                    continue

            # v2.2 Execution Policy enforcement
            if not should_auto_execute(canon_name):
                confirm_payload = {
                    "type": "confirm",
                    "action": u"\u6267\u884c " + (canon_name or tool_name or "").replace("maya.", ""),
                    "target": canon_name,
                    "options": [u"\u6267\u884c", u"\u53d6\u6d88"],
                    "tool": canon_name,
                    "args": args,
                }
                # Record in history so LLM context stays intact
                messages.append({"role": "assistant", "content":
                    _json_module.dumps(confirm_payload, ensure_ascii=False)})
                return confirm_payload, _clean_history(messages)

            friendly_name = canon_name.replace("maya.", "") if canon_name else tool_name
            _emit(u"AI 执行中: %s" % friendly_name)

            if content.strip():
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": "assistant", "content": _json_module.dumps(
                    {"type": "tool_call", "name": tool_name, "arguments": args}, ensure_ascii=False)})

            def _do():
                try:
                    return call_tool(canon_name, args)  # use canon_name so aliases resolve correctly
                except ConfirmationError as ce:
                    raise ce

            # Dedup: only prevent SINGLE_SHOT_TOOLS from re-running
            # (allows 'create sphere' x2, but prevents double-firing of complex single-shot ops)
            if canon_name in _SINGLE_SHOT_TOOLS:
                try:
                    sig = (canon_name, _json_module.dumps(args, sort_keys=True, ensure_ascii=False))
                except Exception:
                    sig = (canon_name, str(args))
                if sig in executed:
                    messages.append({"role": "user", "content":
                        "[TOOL_RESULT]\ntool: %s\nok: true\nresult: null\nerror: null\n[/TOOL_RESULT]" % canon_name})
                    continue
                executed.add(sig)

            # Execution policy: non-auto tools must be confirmed by user via natural language;
            # the LLM is instructed in the system prompt, so we trust its judgment here.
            # For tools that require the LLM to have asked first, the system prompt handles it.
            # Execute on main thread using generalized executor pipeline:
            try:
                from .agent_runtime.plan_executor import _call_tool_on_main_thread
                tool_result = _call_tool_on_main_thread(canon_name, args)
            except ConfirmationError as ce:
                confirm_payload = {
                    "type": "confirm",
                    "action": ce.action,
                    "target": ce.target,
                    "options": ce.options,
                    "tool": tool_name,
                    "args": args,
                }
                messages.append({"role": "assistant", "content": _json_module.dumps(confirm_payload, ensure_ascii=False)})
                return confirm_payload, _clean_history(messages)

            ok = bool(tool_result.get("ok"))
            log.info(u"Tool result: ok=%s | tool=%s", ok, canon_name)

            # Update memory
            if ok:
                _update_memory_from_result(canon_name, tool_result)
                try:
                    EntityMemory.update_last_action(canon_name)
                except Exception:
                    pass

            if ok:
                res_json = _json_module.dumps(tool_result.get("result"), ensure_ascii=False, sort_keys=True)
                err_json = "null"
            else:
                res_json = "null"
                err_json = _json_module.dumps(tool_result.get("error"), ensure_ascii=False, sort_keys=True)

            tool_result_msg = (
                "[TOOL_RESULT]\ntool: {t}\nok: {ok}\nresult: {r}\nerror: {e}\n[/TOOL_RESULT]"
            ).format(t=canon_name, ok=str(ok).lower(), r=res_json, e=err_json)
            messages.append({"role": "user", "content": tool_result_msg})

            # Single-shot tools: return immediately with smart suggestion
            if canon_name in _SINGLE_SHOT_TOOLS:
                suggestion = _get_suggestion(canon_name)
                if ok:
                    summary = {k: v for k, v in tool_result.get("result", {}).items() if k != "summary"}
                    raw_res = "<SINGLE_SHOT_SUCCESS> Result: %s. Suggestion: %s" % (summary, suggestion)
                else:
                    raw_res = "<SINGLE_SHOT_FAILED> Error: %s" % tool_result.get("error")
                
                _emit(u"AI 生成自然语言回复...")
                narration_messages = _build_narration_messages(effective_text, raw_res)
                
                payload = {
                    "provider": provider,
                    "api_key": api_key,
                    "model": model,
                    "messages": narration_messages,
                    "temperature": temperature,
                    "max_output_tokens": 1024,
                }
                
                try:
                    resp = post_json(gateway_url + "/chat", payload, timeout_s=30)
                    if resp:
                        final_text = (
                            resp.get("content")
                            or resp.get("message", {}).get("content", raw_res)
                        )
                    else:
                        final_text = raw_res
                except Exception as e:
                    log.error("Narration failed: %s", e)
                    final_text = raw_res
                
                return {
                    "type": "text",
                    "content": final_text,
                }, _clean_history(messages)
            continue

        raise AgentError(u"\u672a\u77e5\u54cd\u5e94 type\uff1a%s" % resp.get("type"))
