# -*- coding: utf-8 -*-
from __future__ import absolute_import
import re

# 简单任务：单个工具就能搞定的
_SIMPLE_PATTERNS = [
    # 纯创建单个物体
    (r"^(创建|做|来|生成|放)(一个|个)?(球|立方|方块|平面|地面|柱|摄像机|灯光)$", "SIMPLE_TOOL"),
    # 纯查询
    (r"(场景.*有什么|选中了什么|摄像机列表)", "SIMPLE_TOOL"),
    # 单一动画
    (r"^(让|使).+(旋转|弹跳)$", "SIMPLE_TOOL"),
]

# 复杂度信号词
_COMPLEXITY_SIGNALS = {
    # 多步骤信号
    "sequential": [
        u"然后", u"再", u"接着", u"之后", u"并且", u"同时", u"并",
        "then", "and then", "after that", "also",
    ],
    # 空间关系信号
    "spatial": [
        u"在上", u"上面", u"下面", u"旁边", u"中间", u"围绕", u"对着",
        u"面向", u"看向", u"跟随", u"追踪", u"朝向",
        "on top", "beside", "around", "facing", "looking at",
    ],
    # 物理/运动语义信号
    "physics": [
        u"滚动", u"滑动", u"掉落", u"飘动", u"碰撞", u"反弹",
        "roll", "slide", "fall", "float", "bounce",
    ],
    # 关联性信号（B依赖A的结果）
    "dependency": [
        u"一直看向", u"跟随", u"对准", u"绑定", u"约束", u"连接",
        "aim at", "follow", "track", "constrain", "attach",
    ],
}

def analyze_task(user_text):
    """
    判断任务复杂度。
    
    Returns:
        "SIMPLE_TOOL"  — 单工具可完成，走确定性管线
        "COMPLEX_TASK" — 多步骤 / 有空间物理推理，走 LLM 智能规划
    """
    text = user_text.strip()

    # ── 1. 简单模式：完全匹配则快速返回 ──
    for pattern, result in _SIMPLE_PATTERNS:
        if re.search(pattern, text):
            return result

    # ── 2. 复杂度信号评分 ──
    score = 0
    matched_dims = []

    # 2a. 多动作动词检测
    action_verbs = re.findall(
        u"(创建|做|生成|放|让|使|设置|添加|调整|删除|移动|旋转|滚动|弹跳|看向|对准)",
        text
    )
    if len(action_verbs) >= 2:
        score += 2
        matched_dims.append("multi_action(%d)" % len(action_verbs))

    # 2b. 各维度关键词检测
    for dim, keywords in _COMPLEXITY_SIGNALS.items():
        for kw in keywords:
            # 中文直接 in，英文用 lower
            if all(ord(c) < 128 for c in kw):
                hit = kw.lower() in text.lower()
            else:
                hit = kw in text
            if hit:
                score += 1
                matched_dims.append(dim)
                break  # 每个维度只计一次

    if score >= 2:
        return "COMPLEX_TASK"

    # ── 3. 批量创建检测 ──
    text_nums = re.sub(u'[一二三四五六七八九十百千万两]', '1', text)
    found_nums = [int(n) for n in re.findall(r'\d+', text_nums)]
    is_plural = any(n > 1 for n in found_nums)

    if is_plural or u"批量" in text or u"多个" in text:
        create_kws = [u"创建", u"生成", u"做", u"来", "create", "make", "add", "spawn"]
        if any(kw in text.lower() or kw in text for kw in create_kws):
            return "COMPLEX_TASK"

    # ── 4. 单独的动作关键词（兜底） ──
    action_kws = [u"滚", u"跳", u"转", u"围绕", u"绕", u"看向",
                  u"对准", u"约束", u"碎", u"散布", u"铺满"]
    if any(kw in text for kw in action_kws):
        return "COMPLEX_TASK"

    return "SIMPLE_TOOL"
