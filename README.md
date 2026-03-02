## AIFORMAYA（Maya 2020 内置 AI 助手，DeepSeek + Gemini）

这是一个可快速部署的 MVP：

- **Maya 2020（Windows）插件端**：Python 2.7 + PySide2，提供 Dock 面板 + tool-call 执行器（白名单工具）
- **本机 AI 网关**：Python 3 + HTTP（FastAPI），对接 **DeepSeek**（OpenAI 兼容）与 **Gemini**

插件端只负责 UI 与执行 Maya 操作；联网调用全部交给本机网关，稳定、易分发。

---

## 目录结构

- `bridge/`：本机 AI 网关（Python 3）
- `maya_module/`：Maya Module（.mod + scripts）

---

## 1) 启动本机 AI 网关（Python 3）

在 PowerShell 里：

```powershell
cd d:\Work\Hehep\AIFORMAYA\bridge
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 配置环境变量（示例：DeepSeek）
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_API_KEY  = "填你的key"

# 配置环境变量（示例：Gemini）
$env:GEMINI_API_KEY = "填你的key"

python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

---

## 2) 安装 Maya Module

你有两种方式：

### 方式 A（推荐）：设置 MAYA_MODULE_PATH

把 `d:\Work\Hehep\AIFORMAYA\maya_module` 加到环境变量 `MAYA_MODULE_PATH`。

### 方式 B：复制到 Maya 用户 modules 目录

把整个 `maya_module\AIFORMAYA` 目录复制到：

`%USERPROFILE%\Documents\maya\2020\modules\AIFORMAYA`

（确保该目录下有 `AIFORMAYA.mod`）

---

## 3) 在 Maya 里启动面板

打开 Maya 后，在 Script Editor（Python）执行：

```python
import aiformaya
aiformaya.show()
```

如果你想在启动时自动加载，可以把 `userSetup.py` 里加一行 `import aiformaya`（MVP 里暂不自动修改）。

---

## 4) 基本用法

在面板里：

- 选择 Provider：DeepSeek / Gemini
- 设置 Model（网关会按 provider 使用对应字段）
- 确保 `Gateway URL` 为 `http://127.0.0.1:8765`

你可以让 AI 执行：

- “选中相连面（shell）并扩展两圈”
- “把选中物体重命名为 prop_001 起”
- “在当前帧给选中控制器打 transform key”
- “删除 1-24 帧的 transform keys”
- “对 playback range 做 euler filter”

