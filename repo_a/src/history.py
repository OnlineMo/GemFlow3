from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import ensure_dir, read_json, write_json, normalize_topic
from .logger import get_logger
from .config import get_settings

LOG = get_logger(__name__)

HISTORY_PATH = Path("repo_a") / "state" / "history.json"


@dataclass(frozen=True)
class HistoryRecord:
    fingerprint: str
    date: str
    topic_norm: str
    topic: str
    category: str
    edition: int
    relpath: str  # POSIX path to file in repo B
    status: str   # pending ok failed
    run_id: str


def _load_raw() -> Dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {"records": {}}
    data = read_json(HISTORY_PATH, default=None)
    if not isinstance(data, dict):
        return {"records": {}}
    records = data.get("records") or {}
    if not isinstance(records, dict):
        records = {}
    return {"records": records}


def _save_raw(data: Dict[str, Any]) -> None:
    ensure_dir(HISTORY_PATH.parent)
    write_json(HISTORY_PATH, data)


def has_fingerprint(fp: str) -> bool:
    data = _load_raw()
    return fp in data["records"]


def put_record(rec: HistoryRecord) -> None:
    data = _load_raw()
    data["records"][rec.fingerprint] = asdict(rec)
    _save_raw(data)


def get_record(fp: str) -> Optional[Dict[str, Any]]:
    data = _load_raw()
    return data["records"].get(fp)


def compute_fingerprint(topic: str, date: str, edition: int) -> str:
    from .utils import fingerprint as _fp
    return _fp(topic, date, edition)


def next_available_edition(topic: str, date: str, start: int = 1, max_iter: int = 50) -> int:
    """
    在当日为同一主题寻找一个未占用的版次号。
    """
    edition = max(1, int(start))
    for _ in range(max_iter):
        fp = compute_fingerprint(topic, date, edition)
        if not has_fingerprint(fp):
            return edition
        edition += 1
    raise RuntimeError("Too many editions for the same topic in one day")


def record_status(
    *,
    topic: str,
    category: str,
    date: str,
    edition: int,
    relpath: str,
    status: str,
    run_id: str,
) -> str:
    """
    写入或更新一条记录, 返回该记录指纹。
    """
    topic_norm = normalize_topic(topic)
    fp = compute_fingerprint(topic_norm, date, edition)
    rec = HistoryRecord(
        fingerprint=fp,
        date=date,
        topic_norm=topic_norm,
        topic=topic,
        category=category,
        edition=edition,
        relpath=relpath,
        status=status,
        run_id=run_id,
    )
    put_record(rec)
    LOG.info("history_record", extra={"fingerprint": fp, "status": status, "relpath": relpath})
    return fp