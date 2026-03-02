from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional
import time

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="AIFORMAYA Bridge", version="0.1.0")


# -----------------------------
# Models
# -----------------------------

class ToolSchema(BaseModel):
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    provider: str  # "deepseek" | "gemini"
    model: str
    gateway_session_id: Optional[str] = None  # 预留：后续做会话持久化
    messages: List[Dict[str, Any]]
    tools: List[ToolSchema] = Field(default_factory=list)
    temperature: float = 0.2
    max_output_tokens: Optional[int] = None


class ChatResponse(BaseModel):
    # type: "message" | "tool_call"
    type: str
    content: Optional[str] = None
    name: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None


# -----------------------------
# Prompt helpers (统一 tool_call 协议)
# -----------------------------

TOOL_CALL_INSTRUCTIONS = (
    "你是一个 Maya 2020（Windows）里的 AI 助手，专注建模与动画。\n"
    "当你需要 Maya 执行操作时，你必须只输出一个 JSON（不要输出多余文字），格式为：\n"
    "{\"type\":\"tool_call\",\"name\":\"maya.xxx\",\"arguments\":{...}}\n"
    "当你不需要调用工具时，输出自然语言。\n"
    "你只能调用 tools 列表中的工具。若工具名不确定，应先用自然语言询问或调用 maya.list_tools 获取清单，不要随意编造。\n"
    "若用户提出“创建+动画”这类复合需求，优先调用单步宏工具（如 maya.create_and_animate_translate_x），减少往返与重复风险。\n"
    "如果用户请求不安全/影响范围过大，先询问或建议缩小范围。\n"
)


def _build_system_message(tools: List[ToolSchema]) -> Dict[str, Any]:
    tools_dump = [t.model_dump() for t in tools]
    return {
        "role": "system",
        "content": TOOL_CALL_INSTRUCTIONS
        + "\n可用工具（name/description/input_schema）：\n"
        + json.dumps(tools_dump, ensure_ascii=False),
    }


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}\s*$", re.MULTILINE)


def _try_parse_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """
    允许模型输出纯 JSON 或结尾带 JSON。我们取最后一个 { ... } 尝试解析。
    """
    if not text:
        return None
    m = _JSON_BLOCK_RE.search(text.strip())
    if not m:
        return None
    candidate = m.group(0)
    try:
        obj = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "tool_call":
        return None
    if not isinstance(obj.get("name"), str):
        return None
    args = obj.get("arguments", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return {"type": "tool_call", "name": obj["name"], "arguments": args}


# -----------------------------
# Providers
# -----------------------------

class DeepSeekOpenAICompat:
    def __init__(self, base_url: str, api_key: str, timeout_s: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def chat(self, *, model: str, messages: List[Dict[str, Any]], temperature: float, max_output_tokens: Optional[int]) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens

        retries = 3
        backoff = 1.0
        for i in range(retries):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=self.timeout_s)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"]
            except requests.RequestException:
                if i < retries - 1:
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                raise


class GeminiRest:
    """
    Gemini REST：不同版本字段可能略有差异。此处实现一个兼容面较广的最小调用。
    你可通过环境变量覆盖 endpoint。
    """

    def __init__(self, api_key: str, endpoint: str, timeout_s: int = 60):
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s

    def chat(self, *, model: str, messages: List[Dict[str, Any]], temperature: float, max_output_tokens: Optional[int]) -> str:
        # 将 messages 拼为一个文本对话，减少 function calling 差异
        lines = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            lines.append(f"{role}: {content}")
        prompt = "\n".join(lines)

        url = f"{self.endpoint}/{model}:generateContent?key={self.api_key}"
        gen_cfg: Dict[str, Any] = {"temperature": temperature}
        if max_output_tokens is not None:
            gen_cfg["maxOutputTokens"] = int(max_output_tokens)

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }
        retries = 3
        backoff = 1.0
        data = None
        for i in range(retries):
            try:
                r = requests.post(url, json=payload, timeout=self.timeout_s)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except requests.RequestException:
                if i < retries - 1:
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue
                raise

        # 兼容提取文本
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            return json.dumps(data, ensure_ascii=False)


def _get_provider(req: ChatRequest):
    provider = req.provider.strip().lower()
    if provider == "deepseek":
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=400, detail="Missing env DEEPSEEK_API_KEY")
        return DeepSeekOpenAICompat(base_url=base_url, api_key=api_key)

    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=400, detail="Missing env GEMINI_API_KEY")
        endpoint = os.environ.get("GEMINI_ENDPOINT", "https://generativelanguage.googleapis.com/v1beta/models")
        return GeminiRest(api_key=api_key, endpoint=endpoint)

    raise HTTPException(status_code=400, detail=f"Unsupported provider: {req.provider}")


# -----------------------------
# Routes
# -----------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/debug/env")
def debug_env():
    return {
        "deepseek": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "deepseek_base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "gemini_endpoint": os.environ.get("GEMINI_ENDPOINT", "https://generativelanguage.googleapis.com/v1beta/models"),
    }

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    provider = _get_provider(req)

    # 插入 system（把工具清单与 tool_call 协议写死）
    system_msg = _build_system_message(req.tools)
    messages = [system_msg] + req.messages

    try:
        text = provider.chat(
            model=req.model,
            messages=messages,
            temperature=req.temperature,
            max_output_tokens=req.max_output_tokens,
        )
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bridge error: {str(e)}")

    tool_call = _try_parse_tool_call(text)
    if tool_call:
        # 简单校验：name 必须在 tools 列表中
        tool_names = {t.name for t in req.tools}
        if tool_call["name"] not in tool_names:
            # 返回文本提示，让 Maya 端当 message 展示（便于调试）
            return ChatResponse(
                type="message",
                content="模型返回了不存在的工具名：%s。请重试或检查 tools 列表。原始输出：\n%s"
                % (tool_call["name"], text),
            )
        return ChatResponse(type="tool_call", name=tool_call["name"], arguments=tool_call["arguments"])

    return ChatResponse(type="message", content=text)

