## AIFORMAYA（Maya 2020 内置 AI 助手，支持 DeepSeek + Gemini）

这是一个全功能、高扩展性的 Maya AI 智能助手插件。它能够听懂自然语言指令，并在 Maya 内部完成复杂的自动化操作乃至生产指导。

- **Maya 2020（Windows）插件端**：Python 2.7 + PySide2，提供现代化的 Dock 聊天面板，支持自定义工具和沙盒 Python 代码直接执行。
- **本机 AI 网关**：Python 3 + HTTP（FastAPI），对接 **DeepSeek**（极大优化）与 **Gemini**，彻底解决大语言模型直接对接客户端时的鉴权和依赖繁琐问题。

---

## 核心特性 (Key Features)

### 1. 极致灵活的动态执行 (`maya.execute_python_code`)
摆脱了传统“白名单 API”的束缚。遇到内置工具链无法直接完成的非常规指令，AI（被设定为资深 TD 角色）会在后台自动编写合乎逻辑的 Maya Python (`cmds`, `mel`) 脚本并即时运行，满足几乎任何创意需求。

### 2. 生产安全第一 (`maya.ask_user_confirmation`)
系统具备主动安全意识。当 AI 解析到“删除整个场景”、“重置进度”或者“添加参数含糊不清的修改器”时，它将暂缓执行危险命令，并在 UI 面板上弹出**二次确认卡片**（Confirmation Card），将选择权和不同参数的决定权交还给用户。

### 3. 先进的上下文记忆 (Entity Memory)
打破了大模型无法连续跨句子选中物体的魔咒。内置的 `EntityMemory` 会记录每次操作产生的新物体（例如：“创建一个摄像机” -> “让**它**抖动”）。系统能在多轮对话中隐式映射“那个球”、“刚才建的灯光”，大幅提升指令连续性。

### 4. 健壮的连续对话与防截断机制
- 强制使用 `max_output_tokens=4096` 传递给 Gateway，结合网关级别的解析重试，彻底杜绝复杂长步骤推理时发生的 JSON 截断宕机。
- 多轮工具调用（Tool Calls）能够平滑流转，且 AI 的每步推理思考（Reasoning）都会在聊天框内实时展示给用户。

### 5. 现代化交互体验 (Rich Dock UI)
- **对话持久化**：关闭面板或重启 Maya 后，前 200 轮交互记录无损恢复。
- **美观的渲染**：区分你、系统、AI的聊天气泡；支持基础 Markdown (加粗、代码块格式化过滤) 以及自适应夜间护眼配色。
- **一键启动网关**：从此告别手动切终端跑服务的麻烦，直接在 Maya Dock 面板一键检测和唤出本地 Python 3 网关进程。

---

## 目录结构

- `bridge/`：本机 AI 网关（基于 Python 3 + FastAPI）
- `maya_module/`：Maya 插件实现逻辑（.mod + scripts）
- `icon/`：界面所需的图标资源

---

## 安装与启动流程

### 1) 初始环境配置 (一键安装)
请确保你的电脑上安装了 Python 3（供网关服务使用）。
你只需要在 Maya 的脚本编辑器 (Script Editor -> Python) 中执行下一行代码，即可全自动安装插件、配置菜单并在工作区停靠面板：

```python
import sys; sys.path.insert(0, r"你的实际路径\AIFORMAYA"); import ins_aiformaya; reload(ins_aiformaya); ins_aiformaya.install()
```

### 2) 配置 API Key 并启动网关
打开面板后：
1. 点击右上角⚙️图标进入设置，填入你的 **DeepSeek API Key** 或 **Gemini API Key**。
2. 配置好想要使用的模型版本（推荐使用 `deepseek-chat` 或 `gemini-1.5-pro` 体验完整逻辑闭环）。
3. 返回主面板点击上方的 **Start Gateway**，等待几秒钟状态变为 `Connected`。

### 3) 尽情使唤你的专属 TD
在底部的聊天框直接下达全中文/英文指令。
*示例：*
- “帮我创建一个小球和一个摄像机，并让它看向小球”
- “把场景里名字带 'prop_' 的物体全部打个组” （触发动态 Python 执行）
- “给我添加一个手持摄像机的随机抖动动画” （会弹出确认框）
- “把整个场景都删了” （触发危险操作拦截警告）

---

### *如果你需要手动启动网关调试 (开发者模式)*
如果你需要查看看网关输出或开发新功能，可以在 PowerShell 里独立启动：

```powershell
cd bridge
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 设置环境变量然后运行
$env:DEEPSEEK_API_KEY = "sk-xxxxxxxx"
python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

