from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Iterable

import orjson
from slugify import slugify
from zoneinfo import ZoneInfo


# ---------- Paths & IO ----------

def ensure_dir(path: str | Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    data = p.read_bytes()
    try:
        return orjson.loads(data)
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))


def read_text(path: str | Path, default: str = "") -> str:
    p = Path(path)
    if not p.exists():
        return default
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(path: str | Path, content: str) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(content, encoding="utf-8")


# ---------- Time ----------

def today_str(tz: str = "Asia/Shanghai") -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


# ---------- Normalization & Slug ----------

def normalize_topic(text: str) -> str:
    """
    归一化主题用于指纹生成:
    - 去首尾空白
    - 折叠多空白为一个空格
    - 全角空格归一
    - 小写化
    """
    t = (text or "").strip()
    t = t.replace("\u3000", " ")
    t = re.sub(r"\s+", " ", t)
    return t.lower()


def category_slug(category: str) -> str:
    """
    分类目录使用 slug 化名称。为跨平台路径安全, 采用 ascii slug。
    若 slug 为空则回退为 'uncategorized'。
    """
    s = slugify(category or "uncategorized", lowercase=True)
    return s or "uncategorized"


def topic_slug(topic: str) -> str:
    """
    报告文件名的主题部分使用 ascii slug, 避免特殊字符与空格。
    """
    s = slugify(topic or "topic", lowercase=True)
    return s or "topic"


# ---------- Hash & Fingerprint ----------

def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def fingerprint(topic: str, date_str: str, edition: int) -> str:
    """
    指纹 = SHA256(规范化主题 + '\n' + 日期 + '\n' + 版次数字)
    """
    payload = f"{normalize_topic(topic)}\n{date_str}\n{edition}"
    return sha256_hex(payload)


def content_hash(text: str) -> str:
    """
    用于避免空 diff 提交的内容哈希。
    """
    return sha256_hex(text)


# ---------- Filenames & Paths ----------

def report_filename(topic: str, date_str: str, edition: int) -> str:
    """
    文件名: {slugified_主题}-{日期}--v{版次}.md
    """
    return f"{topic_slug(topic)}-{date_str}--v{edition}.md"


def report_relpath(category: str, topic: str, date_str: str, edition: int) -> Path:
    """
    相对路径: AI_Reports/<分类slug>/<filename>
    """
    return Path("AI_Reports") / category_slug(category) / report_filename(topic, date_str, edition)


# ---------- README 占位块更新 ----------

def replace_block(content: str, block_name: str, new_block: str) -> str:
    """
    在 README 中用标记更新块:
    <!-- BEGIN {block_name} -->
    ...old...
    <!-- END {block_name} -->
    若不存在则追加到末尾。
    """
    begin = f"<!-- BEGIN {block_name} -->"
    end = f"<!-- END {block_name} -->"
    if begin in content and end in content:
        pattern = re.compile(
            rf"({re.escape(begin)})(.*?)(\s*{re.escape(end)})",
            flags=re.DOTALL,
        )
        return pattern.sub(lambda _: f"{begin}\n{new_block}\n{end}", content, count=1)
    else:
        sep = "\n\n" if content and not content.endswith("\n") else ""
        return f"{content}{sep}{begin}\n{new_block}\n{end}\n"


# ---------- Small helpers ----------

@dataclass(frozen=True)
class ReportMeta:
    category: str
    topic: str
    date: str
    edition: int
    relpath: str  # POSIX style


def to_posix(p: Path | str) -> str:
    return str(Path(p).as_posix())


def ensure_utf8(s: str) -> str:
    # 简单占位, 未来可扩展编码修复策略
    return s