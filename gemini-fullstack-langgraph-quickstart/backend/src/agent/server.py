from __future__ import annotations

import pathlib
import os
import json
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.routing import Route
from pydantic import BaseModel

from langchain_core.messages import AnyMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agent.graph import graph

app = FastAPI()


def create_frontend_router(build_dir="../frontend/dist"):
    build_path = pathlib.Path(__file__).parent.parent.parent / build_dir
    if not build_path.is_dir() or not (build_path / "index.html").is_file():
        async def dummy_frontend(request):
            return Response(
                "Frontend not built. Run 'npm run build' in the frontend directory.",
                media_type="text/plain",
                status_code=503,
            )
        return Starlette(routes=[Route("/{path:path}", endpoint=dummy_frontend)])
    return StaticFiles(directory=build_path, html=True)


app.mount("/app", create_frontend_router(), name="frontend")


def _to_lc_message(item: Any) -> AnyMessage:
    if isinstance(item, dict):
        role = item.get("role") or item.get("type")
        content = item.get("content", "")
        if role in ("user", "human", "HumanMessage"):
            return HumanMessage(content=content)
        elif role in ("assistant", "ai", "AIMessage"):
            return AIMessage(content=content)
    if isinstance(item, str):
        return HumanMessage(content=item)
    return HumanMessage(content=str(item))


def _msg_to_dict(msg: AnyMessage) -> Dict[str, str]:
    typ = "ai" if isinstance(msg, AIMessage) else "human"
    return {"type": typ, "content": getattr(msg, "content", "")}


class InvokeRequest(BaseModel):
    input: Dict[str, Any]
    config: Optional[Dict[str, Any]] = None


class ClassifyRequest(BaseModel):
    text: str
    candidates: Optional[List[str]] = None
    language: Optional[str] = "zh-CN"
    extra_instructions: Optional[str] = None


class ClassifyResponse(BaseModel):
    category: str
    confidence: float

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/classify")
async def classify(req: ClassifyRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # 候选分类列表（允许调用方传入；缺省时使用内置集合）
    candidates = [c.strip() for c in (req.candidates or []) if isinstance(c, str) and c.strip()]
    if not candidates:
        candidates = [
            "人工智能和机器学习",
            "大型语言模型",
            "软件开发与工程",
            "网络安全",
            "云和 DevOps",
            "数据和数据库",
            "网络和移动",
            "消费电子和硬件",
            "游戏与互动",
            "区块链与加密",
            "科学与太空",
            "医疗保健与生物技术",
            "能源与气候",
            "经济与市场",
            "政策与法规",
            "行业与公司",
            "文化与媒体",
            "未分类",
        ]

    def heuristic_choice() -> Dict[str, Any]:
        lc_text = text.lower()
        # 简单包含匹配，命中则较高置信度
        for cand in candidates:
            if cand and (cand.lower() in lc_text):
                return {"category": cand, "confidence": 0.85}
        # 否则回退为最后一项
        return {"category": candidates[-1], "confidence": 0.5}

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        # 无 API Key 时退化为启发式分类
        return heuristic_choice()

    try:
        # 低温度/严格 JSON 输出提示，尽量减少跑长链路的开销
        model_name = os.getenv("CLASSIFIER_MODEL", "gemini-2.0-flash").strip()
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0,
            max_retries=2,
            api_key=api_key,
        )
        instructions = (
            "你是一个分类器。仅从给定候选集中选择一个最适合的分类，"
            "并仅返回JSON：{\"category\":\"<候选之一>\",\"confidence\":<0到1之间的小数>}。"
            "不要输出任何多余文字。"
        )
        prompt = (
            f"{instructions}\n"
            f"候选集合: {', '.join(candidates)}\n"
            f"待分类文本: {text}\n"
            f"{req.extra_instructions or ''}"
        )
        result = llm.invoke(prompt)
        content = getattr(result, "content", "") or ""

        data = None
        try:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(content[start : end + 1])
        except Exception:
            data = None

        if isinstance(data, dict) and isinstance(data.get("category"), str):
            cat = data.get("category", "").strip()
            try:
                conf = float(data.get("confidence", 0.7))
            except Exception:
                conf = 0.7
            if cat not in candidates:
                # 若不在候选中，做一次简单回退
                return heuristic_choice()
            return {"category": cat, "confidence": max(0.0, min(conf, 1.0))}
        # 解析失败 → 启发式
        return heuristic_choice()
    except Exception:
        # LLM 调用失败 → 启发式
        return heuristic_choice()


@app.post("/graphs/agent/invoke")
async def invoke(req: InvokeRequest):
    messages_raw: List[Any] = []
    if isinstance(req.input, dict):
        messages_raw = req.input.get("messages") or req.input.get("input") or []
    if not isinstance(messages_raw, list) or not messages_raw:
        raise HTTPException(status_code=400, detail="input.messages must be a non-empty list")

    messages = [_to_lc_message(x) for x in messages_raw]

    configurable: Dict[str, Any] = {}
    if req.config and isinstance(req.config, dict):
        configurable = req.config.get("configurable") or {}

    state = {
        "messages": messages,
    }

    result = graph.invoke(state, config={"configurable": configurable})

    out_msgs = result.get("messages", [])
    out_msgs_serialized = [_msg_to_dict(m) for m in out_msgs] if isinstance(out_msgs, list) else []
    sources = result.get("sources_gathered", [])
    if not isinstance(sources, list):
        sources = []

    return {"messages": out_msgs_serialized, "sources_gathered": sources}