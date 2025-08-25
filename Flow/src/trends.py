from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import orjson
import requests

from .utils import ensure_dir, read_json, write_json, today_str
from .logger import get_logger
from .config import get_settings

LOG = get_logger(__name__)


CACHE_DIR = Path("Flow") / "daily_trends"


def _cache_path(date_str: str) -> Path:
    return CACHE_DIR / f"{date_str}.json"


def _is_fresh(path: Path, ttl_hours: int = 24) -> bool:
    if not path.exists():
        return False
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return datetime.now() - mtime < timedelta(hours=ttl_hours)
    except Exception:
        return False


def _fetch_hn_top(limit: int = 20) -> List[Dict[str, Any]]:
    """
    使用 Hacker News 顶部新闻作为无密钥热榜来源。
    返回结构: [{"title": "...", "url": "..."}]
    """
    try:
        r = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=5)
        r.raise_for_status()
        ids = r.json() or []
    except Exception as e:
        LOG.error("hn_top_ids_error", extra={"error": repr(e)})
        return []

    items: List[Dict[str, Any]] = []
    for sid in ids[: max(0, limit * 2)]:  # 多取一些避免无效项
        try:
            rr = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5)
            rr.raise_for_status()
            data = rr.json() or {}
            title = (data.get("title") or "").strip()
            url = data.get("url") or f"https://news.ycombinator.com/item?id={sid}"
            if title:
                items.append({"title": title, "url": url})
            if len(items) >= limit:
                break
        except Exception:
            continue

    LOG.info("hn_top_fetched", extra={"items": len(items)})
    return items


def fetch_trends_from_google(date_str: str) -> List[Dict[str, Any]]:
    """
    聚合获取热点（无密钥），当前实现：Hacker News Top Stories。
    可扩展为 GitHub Trending / Google News RSS 等多源。
    """
    LOG.info("fetch_trends_from_sources", extra={"date": date_str})

    items: List[Dict[str, Any]] = []
    try:
        # 1) 免费且稳定：Hacker News 顶部新闻
        items = _fetch_hn_top(limit=20)
    except Exception as e:
        LOG.error("trends_agg_error", extra={"date": date_str, "error": repr(e)})
        items = []

    if not items:
        # 兜底：返回一个通用主题，确保流程可运行，避免每日空跑
        fallback = [{"title": "AI 大模型与智能体最新进展", "url": ""}]
        LOG.info("trends_fallback_used", extra={"date": date_str, "items": len(fallback)})
        return fallback

    return items


def fetch_trends_cached(date_str: Optional[str] = None, ttl_hours: int = 24) -> List[Dict[str, Any]]:
    """
    读取缓存, 若无或过期则拉取新数据并写入缓存。
    """
    date_str = date_str or today_str(get_settings().tz)
    ensure_dir(CACHE_DIR)
    path = _cache_path(date_str)

    # 命中新鲜缓存
    if _is_fresh(path, ttl_hours=ttl_hours):
        data = read_json(path, default=None)
        if isinstance(data, list):
            LOG.info("trends_cache_hit", extra={"date": date_str, "items": len(data)})
            return data

    # 拉取远端
    try:
        data = fetch_trends_from_google(date_str)
        if not isinstance(data, list):
            data = []
    except Exception as e:
        LOG.error("trends_fetch_error", extra={"date": date_str, "error": repr(e)})
        data = []

    # 降级: 若拉取失败且旧缓存存在则返回旧缓存
    if not data and path.exists():
        old = read_json(path, default=[])
        LOG.info("trends_use_stale_cache", extra={"date": date_str, "items": len(old)})
        return old if isinstance(old, list) else []

    write_json(path, data)
    LOG.info("trends_cache_write", extra={"date": date_str, "items": len(data)})
    return data