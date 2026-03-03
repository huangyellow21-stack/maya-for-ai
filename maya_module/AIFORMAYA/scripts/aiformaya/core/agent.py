# -*- coding: utf-8 -*-
from __future__ import absolute_import

import time

import maya.utils as maya_utils

from .http_client import post_json, HttpError
from . import config as cfgmod
from ..tools.registry import tools_schema, call_tool
from ..tools.maya_tools import ConfirmationError

try:
    from .memory import EntityMemory
except ImportError:
    class EntityMemory(object):
        @classmethod
        def get_summary(cls):
            return ""

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
            "你是一个 Maya 2020（Windows）里的资深技术总监（TD）和动画/建模助手，目前处于“编辑模式”。\n"
            "你可以通过提供的工具直接修改场景代码，并应遵循以下最高级别的职业原则：\n\n"
            "1）如果能用现有的专用工具（如 maya.create_object_and_camera_and_aim 等）解决问题，请优先使用它们；\n"
            "2）如果你发现用户请求的具体操作没有对应的专用工具，**你绝对不应该直接回绝说明“我做不到”**。相反，你可以使用 `maya.execute_python_code` 工具自己编写并运行 Maya Python (`import maya.cmds as cmds` 等) 脚本来实现用户的任意需求！你可以像一个真正的 TD 一样灵活编程；\n"
            "3）对于含糊不清的指令，你必须向用户提问澄清，而不是擅自盲目修改场景；\n"
            "4）面对极其复杂的、一次性无法通过几行代码完成的生产级要求（例如“帮我绑定这个高模角色并刷好权重”），你应该诚实地告诉用户这超出了一次自动执行的范围，**然后给出详细的生产步骤指导和建议**；\n"
            "5）强烈注意破坏性操作！如果用户的指令涉及大范围删除、不可逆的操作或重置场景，在执行之前，你**必须**调用 `maya.ask_user_confirmation` 工具弹出二次确认卡片，并强烈建议用户“另存为”新文件保存进度；\n"
            "6）如果请求部分无法完成或报了错，用人话详细解释错误原因，并给出下一步怎么修复的专业指导。\n"
        )
    sys += (
        "\n\n当你收到 [/TOOL_RESULT] 块后：\n"
        "1. 如果仍然需要继续执行后续动作，请继续输出包含工具调用的 JSON 块，不要生成多余的闲聊语言。\n"
        "2. 当你确定所有需要的操作都已经完成，并且没有任何更多的工具需要调用时，请主动、自然、流畅地用中文给用户回复。\n"
        "- 告诉用户你刚刚具体对哪些对象做了什么操作，取得了什么效果。\n"
        "- 绝对不要再输出任何 JSON、TOOL_RESULT 标记、traceback，也绝对不要以“执行回执”这种死板机器人的口吻开头（就像你在和同事面对面汇报工作一样）。"
    )

    mem_summary = EntityMemory.get_summary()
    if mem_summary:
        sys += "\n\n" + mem_summary

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
    api_key = cfg.get("gemini_api_key") if provider == "gemini" else cfg.get("deepseek_api_key")
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
            "api_key": api_key,
            "model": model,
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "max_output_tokens": 4096,
        }

        try:
            resp = post_json(gateway_url + "/chat", payload, timeout_s=60)
        except HttpError as e:
            raise AgentError("网关请求失败：%s" % str(e))

        if not resp or "type" not in resp:
            raise AgentError("网关返回无效响应：%s" % str(resp))

        if resp["type"] == "message":
            text = resp.get("content") or ""
            
            import re
            cleaned_text = re.sub(r"^(最终执行回执：|最终执行回执:|执行回执：|执行回执:|<final_execution_receipt>)", "", text.strip()).strip()
            if not cleaned_text:
                text = "已执行操作完成。"
            else:
                text = cleaned_text

            messages.append({"role": "assistant", "content": text})
            return text, messages

        if resp["type"] == "tool_call":
            tool_name = resp.get("name")
            args = resp.get("arguments") or {}
            content = resp.get("content") or ""

            import json as _json

            if content.strip():
                # 保留 AI 的推理分步说明文本
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": "assistant", "content": _json.dumps({"type": "tool_call", "name": tool_name, "arguments": args}, ensure_ascii=False)})

            # Ensure Maya ops run on main thread
            def _do():
                try:
                    return call_tool(tool_name, args)
                except ConfirmationError as ce:
                    # Reraise out of Maya thread helper so we can catch it
                    raise ce

            # dedup key
            try:
                canon = _ALIAS_MAP.get(tool_name, tool_name)
                sig = (canon, _json.dumps(args, sort_keys=True, ensure_ascii=False))
            except Exception:
                canon = _ALIAS_MAP.get(tool_name, tool_name)
                sig = (canon, str(args))
            if sig in executed:
                messages.append({"role": "user", "content": "DUPLICATE_TOOL_CALL name=%s skipped" % tool_name})
                continue
            executed.add(sig)

            try:
                tool_result = maya_utils.executeInMainThreadWithResult(_do)
            except ConfirmationError as ce:
                # Return custom confirm JSON to UI
                confirm_payload = {
                    "type": "confirm",
                    "action": ce.action,
                    "target": ce.target,
                    "options": ce.options,
                    "tool": tool_name,
                    "args": args
                }
                import json as _json
                text = _json.dumps(confirm_payload, ensure_ascii=False)
                messages.append({"role": "assistant", "content": text})
                return text, messages

            canon_name = _ALIAS_MAP.get(tool_name, tool_name)
            ok = bool(tool_result.get("ok"))
            
            import json as _json
            if ok:
                res_json = _json.dumps(tool_result.get("result"), ensure_ascii=False, sort_keys=True)
                err_json = "null"
            else:
                res_json = "null"
                err_json = _json.dumps(tool_result.get("error"), ensure_ascii=False, sort_keys=True)

            msg = (
                "[TOOL_RESULT]\n"
                "tool: {tool}\n"
                "ok: {ok}\n"
                "result: {result_json}\n"
                "error: {error_json}\n"
                "[/TOOL_RESULT]"
            ).format(
                tool=canon_name,
                ok=str(ok).lower(),
                result_json=res_json,
                error_json=err_json
            )
            messages.append({"role": "user", "content": msg})
            if canon_name in _SINGLE_SHOT_TOOLS:
                if ok:
                    return "已完成：%s" % canon_name, messages
                else:
                    return "工具执行失败：%s" % canon_name, messages
            continue

        raise AgentError("未知响应 type：%s" % resp.get("type"))
