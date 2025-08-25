import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import get_settings


@dataclass
class EngineResult:
    content: str
    sources_gathered: List[Dict[str, Any]]


class DeepResearchClient:
    """
    LangServe LangGraph 引擎客户端
    目标后端: /graphs/agent/invoke
    """

    def __init__(self, base_url: Optional[str] = None, timeout: int = 120) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.api_base_url).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def _invoke_endpoint(self) -> str:
        return f"{self.base_url}/graphs/agent/invoke"

    @retry(
        reraise=True,
        stop=stop_after_attempt(lambda: get_settings().http_max_retries),
        wait=wait_exponential(multiplier=lambda: float(get_settings().http_backoff_seconds)),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        # 兼容 LangServe 返回格式
        data = resp.json()
        if isinstance(data, dict) and "output" in data and isinstance(data["output"], dict):
            return data["output"]
        return data

    @staticmethod
    def _extract_content_and_sources(output: Dict[str, Any]) -> EngineResult:
        """
        兼容多种返回形态:
        - {"messages":[{"type":"ai","content":"..."}], "sources_gathered":[...]}
        - {"content":"...","sources_gathered":[...]}
        """
        content = ""
        # 1) LangChain messages 风格
        msgs = output.get("messages")
        if isinstance(msgs, list) and msgs:
            # 取最后一条 AI 输出
            tail = msgs[-1]
            if isinstance(tail, dict) and "content" in tail:
                content = tail.get("content") or ""
            elif isinstance(tail, str):
                content = tail
        # 2) 直接 content
        if not content and isinstance(output.get("content"), str):
            content = output["content"]

        sources = output.get("sources_gathered") or []
        if not isinstance(sources, list):
            sources = []
        return EngineResult(content=content, sources_gathered=sources)

    @staticmethod
    def _today_str() -> str:
        tz = ZoneInfo(get_settings().tz)
        return datetime.now(tz).date().isoformat()

    def _build_user_prompt(
        self,
        topic: str,
        category: str,
        source_url: Optional[str],
        edition: str = "v1",
    ) -> str:
        """
        将 Code-Map 的 Markdown 结构要求前置到 agent 的 user prompt 中，提升模板贴合度。
        引擎自身会做网络检索与反思，本提示仅用于格式约束与语言风格控制。
        """
        date_str = self._today_str()
        src_line = f"来源: {source_url}" if source_url else "来源: 自动检索与聚合"
        # 提示模型直接生成中文且结构化分节
        prompt = f"""你是一个研究助理，请围绕主题进行深度研究与交叉验证，输出严格遵循以下 Markdown 模板的中文报告:

版次: 头版（{edition}）
日期: {date_str}
主题: {topic}

{src_line}

# 摘要
- 用 3-5 条要点给出研究的核心结论

# 背景
- 解释研究问题的背景与上下文，并标注必要的定义与范围

# 深度分析
- 从多个角度进行论证，并在需要时给出对比或反例

# 数据与引用
- 用条目列出关键来源与链接，引用需要与文中结论相匹配

# 结论与建议
- 给出可执行的结论与建议，标注限制条件与待后续验证的问题

注意:
- 严格使用中文
- 保持可验证性，避免无来源的强结论
- 引擎会提供可用的引用链接，请将其整合到 数据与引用 部分
- 分类提示: {category}
"""
        return prompt

    def generate_markdown(
        self,
        topic: str,
        category: str,
        source_url: Optional[str] = None,
        edition: str = "v1",
        configurable: Optional[Dict[str, Any]] = None,
    ) -> EngineResult:
        """
        调用 LangServe 的 graphs agent invoke，返回 EngineResult:
        - content: 最终答案文本
        - sources_gathered: 引擎收集到并实际使用的来源列表
        """
        user_prompt = self._build_user_prompt(topic=topic, category=category, source_url=source_url, edition=edition)
        payload: Dict[str, Any] = {
            "input": {
                "messages": [
                    {"role": "user", "content": user_prompt}
                ]
            },
            # 可将研究循环等参数通过 configurable 注入，减少时延与成本
            "config": {
                "configurable": {
                    # 可由调用侧覆盖
                    **(configurable or {})
                }
            },
        }
        output = self._post(self._invoke_endpoint(), payload)
        return self._extract_content_and_sources(output)


# 便捷函数
def deepresearch_generate_markdown(
    topic: str,
    category: str,
    source_url: Optional[str] = None,
    edition: str = "v1",
    configurable: Optional[Dict[str, Any]] = None,
) -> EngineResult:
    client = DeepResearchClient()
    return client.generate_markdown(
        topic=topic,
        category=category,
        source_url=source_url,
        edition=edition,
        configurable=configurable,
    )