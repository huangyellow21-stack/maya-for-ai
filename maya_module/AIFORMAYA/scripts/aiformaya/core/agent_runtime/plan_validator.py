# -*- coding: utf-8 -*-
from __future__ import absolute_import

# 爆炸类意图关键词（中英文均覆盖）
_EXPLOSION_KEYWORDS = [
    u"爆炸", u"炸弹", u"炸弹效果", u"爆炸效果", u"爆炸特效", u"火焰爆炸", u"烟花爆炸",
    "explosion", "bomb", "explode", "blast", "boom", "fx explosion",
]

def _contains_explosion_intent(user_intent):
    """检查 user_intent 字符串是否命中爆炸类关键词"""
    if not user_intent:
        return False
    lower = user_intent.lower()
    for kw in _EXPLOSION_KEYWORDS:
        if kw.lower() in lower:
            return True
    return False

def validate_plan(plan, available_tools, user_intent=None):
    """
    Validates the generated plan structure to ensure safety before execution.
    Raises Exception if the plan is malformed or attempts unallowed operations.
    """
    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        raise Exception(u"任务规划格式错误: steps 必须是列表。")

    if len(steps) > 8:
        raise Exception(u"任务规划超出了最大限制（最多允许 8 步）。")

    allowed = {t.get("name") for t in available_tools}
    forbidden = {"maya.delete_selected", "maya.cleanup_scene"}

    for index, step in enumerate(steps):
        tool = step.get("tool")
        args = step.get("args")

        if not tool:
            raise Exception(u"第 %d 步缺少工具名称 (tool)。" % (index + 1))

        if tool not in allowed:
            raise Exception(u"生成了不存在的假象工具: %s" % tool)

        if tool in forbidden:
            raise Exception(u"拒绝执行高危操作: %s" % tool)

        if args is not None and not isinstance(args, dict):
            raise Exception(u"第 %d 步参数格式不正确，必须为字典。" % (index + 1))

    # ── 爆炸保护：若用户意图命中爆炸关键词，plan 中必须有 import_bomb_asset ──
    if _contains_explosion_intent(user_intent):
        step_tools = [s.get("tool", "") for s in steps]
        has_bomb_tool = "maya.import_bomb_asset" in step_tools
        lowlevel_fp = [
            t for t in step_tools
            if t in ("maya.create_sphere", "maya.create_bouncing_ball",
                     "maya.execute_python_code")
        ]
        if not has_bomb_tool and lowlevel_fp:
            raise Exception(
                u"[爆炸保护] 检测到爆炸类用户意图，但 plan 中未使用 maya.import_bomb_asset，"
                u"却出现了 %s。请使用专用爆炸模板工具。" % lowlevel_fp
            )

    return True
