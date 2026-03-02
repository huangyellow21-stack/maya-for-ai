# -*- coding: utf-8 -*-
from __future__ import absolute_import

import time

import maya.utils as maya_utils

from .http_client import post_json, HttpError
from . import config as cfgmod
from ..tools.registry import tools_schema, call_tool


class AgentError(Exception):
    pass


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
    "maya.create_bouncing_ball": "maya.create_bouncing_ball",
    "maya.camera": "maya.create_camera",
}

_SINGLE_SHOT_TOOLS = set([
    "maya.create_and_animate_translate_x",
    "maya.retime_keys",
    "maya.retime_range",
    "maya.create_bouncing_ball",
    "maya.create_loop_rotate",
    "maya.create_ping_pong_translate",
    "maya.import_bomb_asset",
])


def _model_for_provider(cfg):
    provider = (cfg.get("provider") or "deepseek").strip().lower()
    if provider == "gemini":
        return cfg.get("model_gemini") or "gemini-1.5-flash"
    return cfg.get("model_deepseek") or "deepseek-chat"


def _build_messages(user_text, mode):
    if mode == "view":
        sys = (
            "你是一个 Maya 2020（Windows）里的建模与动画助手，目前处于“问询模式”。"
            "在该模式下，你只能使用只读工具查看场景信息，不能修改场景。"
            "如果用户提出的是可以通过工具完成的编辑操作，请用自然语言说明："
            "当前是只读模式，请他在面板中切换到编辑模式后，你就可以帮他完成这些操作。"
        )
    else:
        sys = (
            "你是一个 Maya 2020（Windows）里的建模与动画助手，目前处于“编辑模式”。"
            "在该模式下，你可以通过工具帮用户执行建模与动画操作，并应遵循以下原则："
            "1）面对抽象的动画或建模需求（例如“小球弹跳动画”“让这个物体绕 Y 轴转几圈”“让这个控制器左右来回移动”），先用自然语言简要说明思路和分步操作方案；"
            "2）如果 tools 列表中存在可以直接完成该需求的专用工具或模板（例如 maya.create_bouncing_ball、maya.create_and_animate_translate_x、maya.create_loop_rotate、maya.create_ping_pong_translate 等），在说明思路之后应优先调用这些专用工具来真正执行场景修改；"
            "3）只有在没有合适专用工具时，才使用通用工具（如创建基础几何体、maya.set_translate、maya.set_key 等）组合出简单示范；"
            "4）如果请求无法完成或缺少必要路径/资源，先说明可以做到的部分并等待用户确认后再执行工具；"
            "5）涉及爆炸/炸弹效果资产导入时，优先使用 maya.import_bomb_asset；"
            "6）对于会大面积影响场景的危险操作，应先明确提示风险并确认用户意图，再决定是否执行。"
        )
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user_text},
    ]


def run_chat(user_text, history_messages=None, max_turns=6):
    """
    history_messages: list of {role, content} excluding the system tool list (bridge will handle that)
    Returns: (final_text, new_history_messages)
    """
    cfg = cfgmod.load_config()
    gateway_url = (cfg.get("gateway_url") or "").rstrip("/")
    if not gateway_url:
        raise AgentError("缺少 gateway_url")

    provider = (cfg.get("provider") or "deepseek").strip().lower()
    model = _model_for_provider(cfg)
    temperature = float(cfg.get("temperature", 0.2))

    mode = str(cfg.get("mode", "edit")).strip().lower()
    tools = tools_schema()
    if mode == "view":
        readonly = set([
            "maya.list_tools",
            "maya.list_selection",
            "maya.list_cameras",
            "maya.list_animated_nodes",
        ])
        tools = [t for t in tools if t.get("name") in readonly]

    messages = []
    if history_messages:
        messages.extend(history_messages)
    else:
        messages.extend(_build_messages(user_text, mode))

    if history_messages:
        messages.append({"role": "user", "content": user_text})

    turns = 0
    executed = set()  # dedup consecutive identical tool calls
    while True:
        turns += 1
        if turns > max_turns:
            raise AgentError("tool-call 回合过多，已停止（%d）" % max_turns)

        payload = {
            "provider": provider,
            "model": model,
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
        }

        try:
            resp = post_json(gateway_url + "/chat", payload, timeout_s=60)
        except HttpError as e:
            raise AgentError("网关请求失败：%s" % str(e))

        if not resp or "type" not in resp:
            raise AgentError("网关返回无效响应：%s" % str(resp))

        if resp["type"] == "message":
            text = resp.get("content") or ""
            messages.append({"role": "assistant", "content": text})
            return text, messages

        if resp["type"] == "tool_call":
            tool_name = resp.get("name")
            args = resp.get("arguments") or {}

            # Ensure Maya ops run on main thread
            def _do():
                return call_tool(tool_name, args)

            # dedup key
            try:
                import json as _json
                canon = _ALIAS_MAP.get(tool_name, tool_name)
                sig = (canon, _json.dumps(args, sort_keys=True, ensure_ascii=False))
            except Exception:
                canon = _ALIAS_MAP.get(tool_name, tool_name)
                sig = (canon, str(args))
            if sig in executed:
                messages.append({"role": "assistant", "content": "DUPLICATE_TOOL_CALL name=%s skipped" % tool_name})
                continue
            executed.add(sig)

            tool_result = maya_utils.executeInMainThreadWithResult(_do)

            canon_name = _ALIAS_MAP.get(tool_name, tool_name)
            ok = bool(tool_result.get("ok"))
            msg = "TOOL_RESULT name=%s ok=%s payload=%s" % (canon_name, str(ok), str(tool_result))
            messages.append({"role": "assistant", "content": msg})
            if canon_name in _SINGLE_SHOT_TOOLS:
                if ok:
                    return "已完成：%s" % canon_name, messages
                else:
                    return "工具执行失败：%s" % canon_name, messages
            continue

        raise AgentError("未知响应 type：%s" % resp.get("type"))
