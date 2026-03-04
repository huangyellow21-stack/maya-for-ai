# -*- coding: utf-8 -*-
from __future__ import absolute_import

def validate_plan(plan, available_tools):
    """
    Validates the generated plan structure to ensure safety before execution.
    Raises Exception if the plan is malformed or attempts unallowed operations.
    """
    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        raise Exception(u"任务规划格式错误: steps 必须是列表。")

    if len(steps) > 5:
        raise Exception(u"任务规划超出了最大限制 (最多允许 5 步)。")

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
            
    return True
