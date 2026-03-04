# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import re

from .. import config as cfgmod
from ..http_client import post_json, HttpError

_PLANNER_PROMPT = u"""\
你是 Maya AI 任务规划器。
你的任务是把用户请求拆分为最少步骤的主流 Maya 操作。

规则：
1. 每一步只能调用一个给定的 Maya 工具。工具名称必须完全来自给定工具列表，不允许自行虚构不存在的工具！
2. 当创建对象数量大于 3 个，或带有批量/循环语义时，必须使用 maya.execute_python_code。
3. 严禁生成重复的同类创建步骤（例如连续三步 create_sphere），批量创建必须转化为单步 execute_python_code。
4. 绝对禁止调用 maya.delete_selected 或 maya.cleanup_scene，除非用户明确请求。
5. 优先使用最简单的工具组合，避免生成过度复杂的执行计划。
6. 步骤最多 5 步
7. 生成的内容必须是由紧凑的 JSON 组成，除了合法的 JSON 字符串外，不要输出任何其他字符或 Markdown 代码块！

请仔细阅读以上规则，严格输出标准的 JSON 对象（包含 steps 数组，每个 step 包含 tool 和 args 字段）。

输出格式示例：
{
  "steps": [
    {
      "tool": "maya.execute_python_code",
      "args": {"code": "import maya.cmds as cmds\\nfor i in range(10):\\n    cmds.polySphere()"}
    },
    {
      "tool": "maya.randomize_transforms",
      "args": {}
    },
    {
      "tool": "maya.camera_look_at",
      "args": {"camera": "camera1", "target": "pSphere1"}
    }
  ]
}
"""

def plan_task(user_text, available_tools, gateway_url, provider, api_key, model):
    """
    Call LLM to decompose a multi-step user request into a JSON plan.
    """
    # Build tool docs
    tool_docs = json.dumps(available_tools, ensure_ascii=False, indent=2)
    
    messages = [
        {"role": "system", "content": _PLANNER_PROMPT + u"\n\n当前可用的工具集：\n" + tool_docs},
        {"role": "user", "content": user_text}
    ]
    
    payload = {
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "messages": messages,
        "temperature": 0.1,  # highly deterministic
        "max_output_tokens": 512,  # Planner does not need many tokens
    }
    
    resp = post_json(gateway_url + "/chat", payload, timeout_s=60)
    
    if not resp or resp.get("type") != "message":
        raise Exception(u"任务规划失败，网关未返回有效响应。")
        
    text = resp.get("content", "").strip()
    
    # Extract JSON 
    text = re.sub(r'```(?:json)?', '', text).strip()
    
    try:
        plan = json.loads(text)
        return plan
    except Exception as e:
        raise Exception(u"任务规划解析 JSON 失败：%s\n原文：%s" % (e, text))
