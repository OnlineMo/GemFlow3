from __future__ import annotations

import pathlib
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.routing import Route
from pydantic import BaseModel

from langchain_core.messages import AnyMessage, HumanMessage, AIMessage

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


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


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