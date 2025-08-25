from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config import get_settings
from .utils import normalize_topic


@dataclass(frozen=True)
class CandidateTopic:
    topic: str
    sources: List[str]
    edition_hint: int = 1
    confidence: float = 0.7


# 简易关键词到分类的规则, 可后续用 LLM 替换
_KEYWORD_TO_CATEGORY: List[Tuple[List[str], str]] = [
    (["ai", "人工智能", "大模型", "模型", "llm", "生成式", "多模态"], "AI"),
    (["安全", "攻击", "漏洞", "勒索", "黑客", "安全事件", "挖矿"], "安全"),
    (["开源", "github", "gitlab", "license", "社区"], "开源"),
    (["芯片", "半导体", "晶圆", "gpu", "npu", "asic"], "芯片"),
    (["云", "大数据", "数据湖", "数据仓库", "k8s", "kubernetes", "容器", "云原生"], "云与大数据"),
]


def extract_candidates_via_ai(trends: List[Dict[str, Any]], top_k: int = 5) -> List[CandidateTopic]:
    """
    输入: Google 热榜 items 列表, 每项建议包含 title 与 url
    输出: 候选主题(去重与规则清洗, 保留前 top_k)
    备注: 初版使用规则提取, 后续可改为 LLM 聚合归并热点为研究主题。
    """
    seen = set()
    items: List[CandidateTopic] = []
    for item in trends:
        title = (item.get("title") or "").strip()
        url = item.get("url") or ""
        if not title:
            continue
        key = normalize_topic(title)
        if key in seen:
            continue
        seen.add(key)
        items.append(CandidateTopic(topic=title, sources=[url] if url else [], edition_hint=1, confidence=0.7))
        if len(items) >= top_k:
            break
    return items


def classify_topic(topic: str) -> Tuple[str, float]:
    """
    基于关键词的初步分类, 返回 (category, confidence)
    找不到匹配时使用 settings.category_list 中的最后一类(通常为 '其他')
    """
    settings = get_settings()
    text = normalize_topic(topic)
    for keywords, cat in _KEYWORD_TO_CATEGORY:
        for kw in keywords:
            if kw.lower() in text:
                return cat, 0.8
    # 回退: 简单基于类别名称自身关键字
    for cat in settings.category_list:
        if normalize_topic(cat) in text:
            return cat, 0.6
    # 最后回退为“其他”
    fallback = settings.category_list[-1] if settings.category_list else "其他"
    return fallback, 0.5