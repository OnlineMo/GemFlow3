from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import orjson
import requests
import re
import json

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


def _fetch_baidu_realtime(limit: int = 20) -> List[Dict[str, Any]]:
    """
    抓取百度热榜（实时），解析页面中的 <!-- s-data: ... --> JSON。
    返回结构: [{"title": "...", "url": "..."}]
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.get("https://top.baidu.com/board?tab=realtime", timeout=8, headers=headers)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        LOG.error("baidu_fetch_error", extra={"error": repr(e)})
        return []

    try:
        # 直接匹配含注释的 JSON 段
        m = re.search(r"<!--\s*s-data:(.*?)-->", html, re.DOTALL | re.IGNORECASE)
        if not m:
            # 退化尝试：压缩空白后再匹配（仿 PHP 方案）
            compact = html.replace("\n", "").replace("\r", "").replace(" ", "")
            m = re.search(r"<!--s-data:(.*?)-->", compact, re.DOTALL | re.IGNORECASE)
        if not m:
            LOG.error("baidu_sdata_not_found", extra={})
            return []
        raw_json = m.group(1)
        obj = json.loads(raw_json)
    except Exception as e:
        LOG.error("baidu_sdata_parse_error", extra={"error": repr(e)})
        return []

    items: List[Dict[str, Any]] = []
    try:
        cards = obj.get("data", {}).get("cards", []) if isinstance(obj, dict) else []
        for card in cards:
            content = card.get("content", []) if isinstance(card, dict) else []
            for entry in content:
                title = (entry.get("word") or "").strip()
                url = entry.get("url") or entry.get("appUrl") or ""
                if title:
                    items.append({"title": title, "url": url})
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
    except Exception:
        pass

    # 去重
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for it in items:
        key = (it.get("title") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    LOG.info("baidu_realtime_fetched", extra={"items": len(uniq)})
    return uniq


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
    聚合获取热点（无密钥）：
    1) 首选 百度热榜（实时）
    2) 回退 Hacker News Top Stories
    3) 最终兜底一个通用主题，避免空跑
    """
    LOG.info("fetch_trends_from_sources", extra={"date": date_str})

    items: List[Dict[str, Any]] = []
    # 1) Baidu 实时热榜
    try:
        items = _fetch_baidu_realtime(limit=20)
    except Exception as e:
        LOG.error("trends_baidu_error", extra={"date": date_str, "error": repr(e)})
        items = []

    # 2) 回退 HN
    if not items:
        try:
            items = _fetch_hn_top(limit=20)
        except Exception as e:
            LOG.error("trends_hn_error", extra={"date": date_str, "error": repr(e)})
            items = []

    # 3) 最终兜底
    if not items:
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