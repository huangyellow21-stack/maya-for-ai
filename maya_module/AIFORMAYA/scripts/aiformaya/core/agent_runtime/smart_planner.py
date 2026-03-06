# -*- coding: utf-8 -*-
"""
Smart Planner — 让大模型真正「思考」后生成执行计划

与 generate_plan（确定性模板）不同，这里：
1. 把用户请求 + 可用工具 + 场景上下文 喂给 LLM
2. LLM 进行空间推理、物理推理、依赖分析
3. 输出结构化的执行计划（带精确参数）
4. 解析并返回 plan dict
"""
import json
import logging
import re

log = logging.getLogger("aiformaya")

# ─────────────────────────────────────────────
# 工具能力描述（给 LLM 看的）
# ─────────────────────────────────────────────
TOOL_DESCRIPTIONS = """
## 可用工具清单

### 创建类（会在场景中新建对象）
- maya.create_plane(name, width, height, translate, rotate)
  创建平面。translate=[x,y,z]。

- maya.create_sphere(name, radius, translate)
  创建球体。translate=[x,y,z]。

- maya.create_cube(name, width, height, depth, translate)
  创建立方体。

- maya.create_cylinder(name, radius, height, translate)
  创建圆柱。

- maya.create_camera(name, translate)
  创建摄像机。translate=[x,y,z]，不需要预设 rotate

- maya.create_bouncing_ball(name, height, bounces, start_time, end_time)
  ⚠️ 会创建一个【全新的球体】并自带弹跳动画。
  仅用于"创建一个弹跳球"这种独立请求。
  ❌ 禁止与 create_sphere 同时使用（会产生两个球）
  ❌ 不能用于"滚动"效果

### 🔥 FX 特效 / 高层工作流（【首选】，优先于任何低层工具）
- maya.import_bomb_asset(namespace)
  【爆炸类请求必用】导入预制爆炸 FX 模板资产。
  适用于：创建爆炸/炸弹/炸弹效果/爆炸特效/火焰爆炸/幼炸/炸弹模板
  ❌ 禁止用 create_sphere 或 create_bouncing_ball 代替爆炸
  ❌ 禁止用 execute_python_code 随意拼凁伪爆炸

- maya.create_turntable(target, frames, distance)
  创建环绕目标旋转的展示摄像机（Turntable），自动计算距离。
  适用于：产品展示/模型展示/360度展示/turntable镜头
  target=目标物体name, frames=帧数, distance=采载距离

- maya.create_three_point_lighting(target, intensity)
  创建标准三点布光（主光 + 辅光 + 轮廓光），智能计算光源位置。
  适用于：打光/布光/照亮场景/三点布光

- maya.camera_look_at(camera, target)
  摄像机创建持久 aimConstraint 全程跟随 target（不删除约束）。

### 动画类（仅对已有物体添加动画，不会创建新物体）
- maya.create_and_animate_translate_x(target, start_value, end_value, start_time, end_time)
  对【已有】物体做 X 轴平移动画。target 必须是前面步骤创建的物体 name。

- maya.create_loop_rotate(target, axis, start_time, end_time, rotations)
  对【已有】物体做旋转动画。axis="x"/"y"/"z"。rotations 支持小数和负数。

- maya.create_ping_pong_translate(target, axis, min_value, max_value, start_time, end_time, cycles)
  来回往复平移动画。

### 摄像机类
- maya.camera_look_at(camera, target)
  让摄像机持续看向指定物体（aim constraint，会一直跟踪）。
  摄像机用本地 -Z 轴对准目标，因此创建摄像机时不公设置 rotate（aimConstraint 自动搞定朝向）。
  ⚠️ camera 参数 = 前面 create_camera 的 name 参数（必须完全一致）
  ⚠️ target 参数 = 前面创建的物体的 name 参数（必须完全一致）

- maya.create_turntable(target, frames, distance)
  创建环绕目标旋转的展示摄像机。

### 关键帧类
- maya.set_key(target, attribute, value, time)
  在指定帧设置关键帧。attribute 如 "translateX", "rotateZ" 等。

### 代码类（最后手段）
- maya.execute_python_code(code)
  执行任意 Maya Python 代码。仅在没有对应专用工具时使用。

---

## 常见工作流模板（必须严格遵守）

### 🔴 滚动效果（translate + rotate 联动）
正确做法（先创建，再分别添加平移和旋转动画）：
1. maya.create_sphere → 创建球
   ⚠️ 球必须贴地：若地面位于 y=0，球 radius=r，则必须显式传入 translate=[x, r, z]
   ⚠️ translate 参数不能省略，必须明确指定初始位置
2. maya.create_and_animate_translate_x → X 轴平移（start_value/end_value 是绝对 X 坐标）
3. maya.create_loop_rotate → Z 轴旋转（axis="z"）
   rotations = -(平移距离) / (2 × 3.14159 × 半径)
   负号表示向 +X 滚动时 Z 轴负方向旋转

❌ 错误：用 create_bouncing_ball（那是弹跳不是滚动，而且会多创建一个球）
❌ 错误：创建球时不传 translate（球会潜入地面内）

### 🔴 摄像机跟踪
1. maya.create_camera → 创建摄像机（给一个有意义的 name）
   translate=[x,y,z] 设定初始位置，不需要预设 rotate
2. maya.camera_look_at → camera=摄像机name, target=目标物体name
   创建【持久 aimConstraint】，摄像机沿本地 -Z 轴跟随目标，无需预设旋转
   两个 name 必须与前面创建步骤中的 name 参数完全相同！

### 🔴 球放在平面上
球的 translateY = 平面的 translateY + 球的 radius

## 名称一致性规则（最重要）
- 每个物体在创建时通过 name 参数命名
- 后续所有步骤引用该物体时，必须使用完全相同的 name
- 一个计划中不要创建两个同类型物体（除非用户明确要求）
"""

# ─────────────────────────────────────────────
# Few-shot 示例
# ─────────────────────────────────────────────
FEW_SHOT_EXAMPLE = u"""
## 参考示例

### 示例请求："创建一个地面，一个球在上面滚动，摄像机一直看向球"

```json
{
  "reasoning": "地面在y=0，球半径1放在y=1。滚动=X平移+Z旋转联动。球从x=-5滚到x=5共10个单位，旋转圈数=10/(2×3.14×1)≈1.59，方向为负。摄像机在侧上方，用aim constraint跟踪球。所有后续步骤的target/camera必须与创建时的name一致。",
  "steps": [
    {
      "tool": "maya.create_plane",
      "args": {"name": "ground_plane", "width": 20, "height": 20, "translate": [0, 0, 0]},
      "purpose": "创建地面"
    },
    {
      "tool": "maya.create_sphere",
      "args": {"name": "rolling_ball", "radius": 1, "translate": [-5, 1, 0]},
      "purpose": "创建球，放在地面上(y=0+1=1)"
    },
    {
      "tool": "maya.create_and_animate_translate_x",
      "args": {"target": "rolling_ball", "start_value": -5, "end_value": 5, "start_time": 1, "end_time": 120},
      "purpose": "球沿X轴平移10个单位"
    },
    {
      "tool": "maya.create_loop_rotate",
      "args": {"target": "rolling_ball", "axis": "z", "start_time": 1, "end_time": 120, "rotations": -1.59},
      "purpose": "球绕Z轴旋转模拟滚动，1.59≈10/(2π×1)"
    },
    {
      "tool": "maya.create_camera",
      "args": {"name": "track_cam", "translate": [0, 5, 15]},
      "purpose": "创建跟踪摄像机（不需要预设 rotate， aimConstraint 会自动搞定朝向）"
    },
    {
      "tool": "maya.camera_look_at",
      "args": {"camera": "track_cam", "target": "rolling_ball"},
      "purpose": "摄像机持续看向球"
    }
  ]
}
```

注意上面示例中：
只用了一次 create_sphere，没有用 create_bouncing_ball
"rolling_ball" 在创建、平移动画、旋转动画中完全一致
"track_cam" 在创建摄像机和 camera_look_at 中完全一致
"""

def build_planning_prompt(user_text, scene_context="", tool_descriptions=None):
    """构造让 LLM 进行空间/物理推理的规划 prompt"""

    if tool_descriptions is None:
        tool_descriptions = TOOL_DESCRIPTIONS

    system = u"""你是一个 Maya 3D 场景规划专家。

你的任务：根据用户的自然语言请求，生成一个精确的执行计划。

你必须做到：
空间推理：物体之间的位置关系要正确

球在地面上 → ball.translateY = ground.translateY + ball.radius
物体在另一个物体旁边 → 计算合理的偏移量

物理推理：运动要符合直觉
滚动 = 平移 + 旋转联动（绝对不是弹跳）
滚动旋转圈数 = 平移距离 / (2π × 半径)
弹跳有重力衰减

依赖推理：后续步骤必须引用前面创建的物体名
先创建球 name="my_ball"
后面动画的 target="my_ball"（完全一致）
camera_look_at 的 camera 和 target 必须与创建时的 name 完全一致
参数要具体：不要留空，每个数值都要算好

不要重复创建：如果已经用 create_sphere 创建了球，就不要再用 create_bouncing_ball

输出格式（严格 JSON）：
```json
{
  "reasoning": "简述你的空间/物理推理过程（2-3句话）",
  "steps": [
    {
      "tool": "maya.xxx",
      "args": { ... },
      "purpose": "这一步做什么"
    }
  ]
}
```

重要约束：

只使用可用工具清单中的工具
每个 step 只调用一个工具
args 中的值必须是具体数字/字符串，不能是变量或表达式
name 参数用有意义的英文（如 ground_plane, rolling_ball, track_cam）
同一计划中 name 前后必须完全一致
如果需要多个关键帧动画且没有专用工具，用 maya.execute_python_code 写完整代码

---

## 常见工作流模板（必须严格遵守）

### 🔴 爆炸 / 炸弹 FX
正确做法：指求任何形式的爆炸效果，必须且只能南用：
1. maya.import_bomb_asset → 导入预制爆炸模板资产
   namespace="Bomb"（默认）

❌ 禁止：用 create_sphere + 弹跳来“代替”爆炸
❌ 禁止：用 execute_python_code 拼 FX 动画
❌ 禁止：说“我不会创建爆炸”或“爆炸太复杂了”

### 🔴 滚动效果（translate + rotate 联动）
正确做法（先创建，再分别添加平移和旋转动画）：
1. maya.create_sphere → 创建球
   ⚠️ 球必须贴地：若地面位于 y=0，球 radius=r，则必须显式传入 translate=[x, r, z]
   ⚠️ translate 参数不能省略，必须明确指定初始位置
2. maya.create_and_animate_translate_x → X 轴平移（start_value/end_value 是绝对 X 坐标）
3. maya.create_loop_rotate → Z 轴旋转（axis="z"）
   rotations = -(平移距离) / (2 × 3.14159 × 半径)
   负号表示向 +X 滚动时 Z 轴负方向旋转

❌ 错误：用 create_bouncing_ball（那是弹跳不是滚动，而且会多创建一个球）
❌ 错误：创建球时不传 translate（球会潜入地面内）

### 🔴 产品展示镜头 / Turntable
1. maya.create_turntable → target=目标物体name, frames=帧数, distance=采载距离
   自动内置三点布光；若需独立布光可再加一步 maya.create_three_point_lighting

### 🔴 摄像机跟踪
1. maya.create_camera → 创建摄像机（给一个有意义的 name）
   translate=[x,y,z] 设定初始位置，不需要预设 rotate
2. maya.camera_look_at → camera=摄像机name, target=目标物体name
   创建【持久 aimConstraint】，摄像机沿本地 -Z 轴跟随目标，无需预设旋转
   两个 name 必须与前面创建步骤中的 name 参数完全相同！

### 🔴 球放在平面上
球的 translateY = 平面的 translateY + 球的 radius

## 名称一致性规则（最重要）
- 每个物体在创建时通过 name 参数命名
- 后续所有步骤引用该物体时，必须使用完全相同的 name
- 一个计划中不要创建两个同类型物体（除非用户明确要求）

## 🔴 AI 助手表达规范
你必须如实体现专业工作流，永远不说“我不会”。正确表达：
- “我将为你导入一个预制爆炸模板资产”       (不说“我不会创建爆炸”)
- “我将按更稳定的方式搞定这个镜头”        (不说“这个太复杂了”)
- “我将按正确的滚动物理公式内建动画”      (不说“我只能先给你一个球”)
- “我将把任务拆解为干净的执行步骤”     (不说“我只能随便写一点代码”)

%s

%s
""" % (tool_descriptions, FEW_SHOT_EXAMPLE)

    if scene_context:
        user_msg = u"【当前场景】\n%s\n\n【用户请求】\n%s" % (scene_context, user_text)
    else:
        user_msg = u"【用户请求】\n%s" % user_text

    return system, user_msg


def parse_plan_response(llm_text):
    """从 LLM 回复中提取 JSON 计划"""
    match = re.search(r'```json\s*(.*?)\s*```', llm_text, re.DOTALL)
    if match:
        raw = match.group(1)
    else:
        raw = llm_text.strip()
        start = raw.find('{')
        end = raw.rfind('}')
        if start >= 0 and end > start:
            raw = raw[start:end+1]

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error("Failed to parse plan JSON: %s\nRaw: %s", e, raw[:500])
        return None

    if "steps" not in plan:
        log.error("Plan missing 'steps' key")
        return None

    if not isinstance(plan["steps"], list) or len(plan["steps"]) == 0:
        log.error("Plan 'steps' is empty or not a list")
        return None

    for step in plan["steps"]:
        if "tool" not in step:
            log.error("Step missing 'tool': %s", step)
            return None
        if "args" not in step:
            step["args"] = {}

    # ── Post-parse validation: detect duplicate ball creation ──
    has_create_sphere = any(s.get("tool") == "maya.create_sphere" for s in plan["steps"])
    has_bouncing_ball = any(s.get("tool") == "maya.create_bouncing_ball" for s in plan["steps"])
    if has_create_sphere and has_bouncing_ball:
        log.warning("Plan has both create_sphere and create_bouncing_ball — removing bouncing_ball to avoid duplicate")
        plan["steps"] = [s for s in plan["steps"] if s.get("tool") != "maya.create_bouncing_ball"]

    # ── Post-parse validation: name consistency check ──
    created_names = {}
    for step in plan["steps"]:
        tool = step.get("tool", "")
        args = step.get("args", {})
        # Track created names
        if "create" in tool and "name" in args:
            created_names[args["name"]] = tool
        # Check references
        for ref_key in ("target", "camera"):
            ref_val = args.get(ref_key)
            if ref_val and created_names and ref_val not in created_names:
                # Try to find closest match
                for cn in created_names:
                    if ref_val.lower().replace("_", "") in cn.lower().replace("_", "") or \
                       cn.lower().replace("_", "") in ref_val.lower().replace("_", ""):
                        log.warning("Name mismatch: step references '%s' but created name is '%s'. Auto-fixing.", ref_val, cn)
                        args[ref_key] = cn
                        break

    log.info("Smart plan parsed: %d steps, reasoning: %s",
             len(plan["steps"]), plan.get("reasoning", "")[:100])

    return plan

def validate_smart_plan(plan, available_tools_schema):
    """验证计划中的工具是否都存在，并进行增强安全检查"""
    available_names = {t.get("name") for t in available_tools_schema}

    # CAUTION: function-level import to avoid circular dep with agent.py
    try:
        from ..agent import _ALIAS_MAP
        available_names.update(_ALIAS_MAP.keys())
    except ImportError:
        pass

    # 尝试修复常见的 LLM 输出格式问题（string → list）
    for step in plan.get("steps", []):
        for k, v in step.get("args", {}).items():
            is_str = isinstance(v, str)
            try:
                is_str = is_str or isinstance(v, unicode)
            except NameError:
                pass
            if is_str and v.startswith("[") and v.endswith("]"):
                try:
                    step.get("args", {})[k] = json.loads(v)
                except Exception:
                    pass

    errors = []

    # 校验 1：steps 非空
    steps = plan.get("steps", [])
    if not steps:
        errors.append("Plan has no steps (empty plan)")
        return errors  # 无步骤直接返回，后续校验无意义

    # 校验 2：禁止未解析的占位符（{selection}, {camera} 等）
    for i, step in enumerate(steps):
        for k, v in step.get("args", {}).items():
            is_str = isinstance(v, str)
            try:
                is_str = is_str or isinstance(v, unicode)
            except NameError:
                pass
            if is_str and "{" in v and "}" in v:
                errors.append(
                    "Step %d arg '%s': unresolved placeholder '%s' — "
                    "complex plan must use concrete values, not template variables" % (i+1, k, v)
                )

    # 校验 3：工具名合法性 + python代码标记（不阻断，加标记）
    has_python_code = False
    for i, step in enumerate(steps):
        tool = step.get("tool", "")
        if tool not in available_names:
            errors.append("Step %d: unknown tool '%s'" % (i+1, tool))
        if tool == "maya.execute_python_code":
            has_python_code = True

    # 信息性标记，不属于真正的错误；agent.py 用它设置 risk 等级
    if has_python_code:
        errors.append("__has_python_code__")

    return errors


# ─────────────────────────────────────────────
# UI 摘要生成
# ─────────────────────────────────────────────
_TOOL_TO_HUMAN = {
    "maya.create_plane":                   u"创建平面",
    "maya.create_sphere":                  u"创建球体",
    "maya.create_cube":                    u"创建立方体",
    "maya.create_cylinder":                u"创建圆柱",
    "maya.create_camera":                  u"创建摄像机",
    "maya.camera_look_at":                 u"让摄像机看向目标",
    "maya.camera_frame_selection":         u"摄像机框选目标",
    "maya.create_loop_rotate":             u"添加循环旋转动画",
    "maya.create_ping_pong_translate":     u"添加往复平移动画",
    "maya.create_and_animate_translate_x": u"添加X轴平移动画",
    "maya.create_bouncing_ball":           u"创建弹跳球动画",
    "maya.create_turntable":               u"创建展示转台摄像机",
    "maya.set_key":                        u"设置关键帧",
    "maya.retime_keys":                    u"调整关键帧时间",
    "maya.retime_range":                   u"调整动画范围",
    "maya.execute_python_code":            u"执行批量/高级脚本操作",
    "maya.duplicate_objects":             u"复制对象",
    "maya.freeze_transforms":             u"冻结变换",
    "maya.center_pivot":                  u"居中轴心",
    "maya.delete_selected":               u"删除选中对象",
    "maya.cleanup_scene":                 u"清理场景",
    "maya.scan_scene_summary":            u"扫描场景信息",
    "maya.create_three_point_lighting":   u"创建三点布光",
}

_CREATE_TOOLS = {
    "maya.create_plane", "maya.create_sphere", "maya.create_cube",
    "maya.create_cylinder", "maya.create_camera", "maya.create_bouncing_ball",
    "maya.create_turntable", "maya.create_three_point_lighting",
}
_MODIFY_TOOLS = {
    "maya.create_loop_rotate", "maya.create_ping_pong_translate",
    "maya.create_and_animate_translate_x", "maya.camera_look_at",
    "maya.camera_frame_selection", "maya.set_key", "maya.retime_keys",
    "maya.retime_range", "maya.freeze_transforms", "maya.center_pivot",
    "maya.duplicate_objects",
}
_DELETE_TOOLS = {"maya.delete_selected", "maya.cleanup_scene"}
_HIGH_RISK_KEYWORDS = {"delete", "cleanup", "remove", "del"}


def summarize_plan_for_ui(plan, user_text):
    """
    将 raw LLM plan 转换成前端可读的 UI 结构。

    返回字段：goal, summary, steps(list of str), risk, undoable, estimated_impact
    """
    steps_raw = plan.get("steps", [])
    reasoning = plan.get("reasoning", "")

    # ── goal ──
    # 优先取 reasoning 的第一句，否则用 user_text
    goal = user_text
    if reasoning:
        first_sentence = reasoning.split("。")[0].split(".")[0].strip()
        if first_sentence and len(first_sentence) < 120:
            goal = first_sentence

    # ── human-readable steps ──
    human_steps = []
    for step in steps_raw:
        tool = step.get("tool", "")
        purpose = step.get("purpose", "")
        if purpose:
            human_steps.append(purpose)
        else:
            human_steps.append(_TOOL_TO_HUMAN.get(tool, tool.replace("maya.", "")))

    # ── summary ──
    if human_steps:
        summary = u"将依次执行：" + u"、".join(human_steps[:5])
        if len(human_steps) > 5:
            summary += u" 等 %d 步操作。" % len(human_steps)
        else:
            summary += u"。"
    else:
        summary = u""

    # ── risk ──
    has_python = any(s.get("tool") == "maya.execute_python_code" for s in steps_raw)
    has_delete = any(
        any(kw in (s.get("tool") or "") for kw in _HIGH_RISK_KEYWORDS)
        for s in steps_raw
    )
    if has_delete:
        risk = "HIGH"
    elif has_python:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    # ── estimated_impact ──
    create_count = sum(1 for s in steps_raw if s.get("tool") in _CREATE_TOOLS)
    modify_count = sum(1 for s in steps_raw if s.get("tool") in _MODIFY_TOOLS)
    # execute_python_code 算 modify（不确定性高）
    modify_count += sum(1 for s in steps_raw if s.get("tool") == "maya.execute_python_code")
    delete_count = sum(1 for s in steps_raw if s.get("tool") in _DELETE_TOOLS)

    return {
        "goal": goal,
        "summary": summary,
        "steps": human_steps,
        "risk": risk,
        "undoable": True,
        "estimated_impact": {
            "create": create_count,
            "modify": modify_count,
            "delete": delete_count,
        },
    }
