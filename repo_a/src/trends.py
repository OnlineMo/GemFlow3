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


CACHE_DIR = Path("repo_a") / "daily_trends"


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


def fetch_trends_from_google(date_str: str) -> List[Dict[str, Any]]:
    """
    真实实现可基于 Google Trends API 或第三方服务。
    这里提供一个占位实现, 返回空列表。
    """
    LOG.info("fetch_trends_from_google_placeholder", extra={"date": date_str})
    # 示例结构:
    # [
    #   {"title": "OpenAI new model", "url": "https://trends.google.com/..."},
    #   {"title": "Apple GPT rumors", "url": "https://trends.google.com/..."},
    # ]
    return []


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