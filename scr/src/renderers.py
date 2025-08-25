from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple, DefaultDict
from collections import defaultdict

from slugify import slugify

from .config import get_settings
from .utils import replace_block, to_posix
from .logger import get_logger
from .github_api import GitHubRepoClient

LOG = get_logger(__name__)

NAV_TITLE = "# DeepResearch 报告导航"

REPORT_PATH_PREFIX = "AI_Reports/"
REPORT_REGEX = re.compile(r"^AI_Reports/([^/]+)/(.+)-(\d{4}-\d{2}-\d{2})--v(\d+)\.md$")

THEME_LINE_RE = re.compile(r"^\s*主题:\s*(.+)\s*$", re.MULTILINE)
SOURCE_LINE_RE = re.compile(r"^\s*来源:\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class NavItem:
    category_slug: str
    relpath: str  # posix
    title: str
    date: str  # YYYY-MM-DD
    edition: int
    source_url: Optional[str] = None


def _slug_display_map(categories: List[str]) -> Dict[str, str]:
    """
    将配置中的显示类目转换为 slug -> 显示名 映射
    """
    m: Dict[str, str] = {}
    for name in categories:
        m[slugify(name, lowercase=True) or "uncategorized"] = name
    return m


def _decode_content_field(content_field: str) -> str:
    # GitHub contents API 使用 base64 并带换行
    try:
        raw = base64.b64decode(content_field.encode("ascii"))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _parse_report_path(path: str) -> Optional[Tuple[str, str, str, int]]:
    """
    返回 (category_slug, topic_slug, date, edition)
    """
    m = REPORT_REGEX.match(path)
    if not m:
        return None
    category_slug, topic_slug, date_str, edition_str = m.groups()
    return category_slug, topic_slug, date_str, int(edition_str)


def _fetch_title_and_source(client: GitHubRepoClient, path: str, ref: Optional[str]) -> Tuple[str, Optional[str]]:
    data = client.get_contents(path, ref=ref)
    if not data or not isinstance(data, dict):
        return "", None
    content_field = data.get("content")
    if not isinstance(content_field, str):
        return "", None
    text = _decode_content_field(content_field)
    title = ""
    source = None
    m1 = THEME_LINE_RE.search(text)
    if m1:
        title = m1.group(1).strip()
    m2 = SOURCE_LINE_RE.search(text)
    if m2:
        source = m2.group(1).strip()
    return title, source


def _group_and_pick_latest(
    items: List[NavItem],
    max_per_category: int,
    category_order: List[str],
) -> List[Tuple[str, List[NavItem]]]:
    """
    对每个分类按日期倒序与 edition 倒序取前 N
    """
    buckets: DefaultDict[str, List[NavItem]] = defaultdict(list)
    for it in items:
        buckets[it.category_slug].append(it)

    def sort_key(it: NavItem):
        # 日期倒序, 版次倒序
        return (it.date, it.edition)

    ordered: List[Tuple[str, List[NavItem]]] = []
    # 按配置顺序输出分类, 其余未知分类按字母序
    known = set()
    for display_name in category_order:
        slug = slugify(display_name, lowercase=True) or "uncategorized"
        if slug in buckets:
            lst = sorted(buckets[slug], key=sort_key, reverse=True)[:max_per_category]
            ordered.append((slug, lst))
            known.add(slug)
    for slug, lst in sorted(buckets.items()):
        if slug in known:
            continue
        ordered.append((slug, sorted(lst, key=sort_key, reverse=True)[:max_per_category]))
    return ordered


def generate_navigation_md(client: GitHubRepoClient, max_per_category: int = 20) -> str:
    """
    读取仓库树, 找到 AI_Reports 下的报告文件, 拉取各分类前 N 条文件标题与来源, 生成 NAVIGATION.md 内容。
    """
    ref = client.get_repo_ref()
    tree = client.list_tree(ref.tree_sha, recursive=True)

    # 初步收集路径元数据
    prelim: List[Tuple[str, str, str, int]] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path.startswith(REPORT_PATH_PREFIX):
            continue
        parsed = _parse_report_path(path)
        if not parsed:
            continue
        prelim.append(parsed)  # (cat_slug, topic_slug, date, edition)

    settings = get_settings()
    slug_to_display = _slug_display_map(settings.category_list)

    # 对每个分类挑选前 N 条再读取文件内容获取标题与来源, 降低 API 请求量
    # 先构造 NavItem 占位
    temp_items: List[NavItem] = [
        NavItem(
            category_slug=cat_slug,
            relpath=to_posix(path := PurePosixPath("AI_Reports") / cat_slug / f"{topic_slug}-{date}--v{edition}.md"),
            title=topic_slug,  # 先用 slug, 稍后替换为文件内“主题”
            date=date,
            edition=edition,
            source_url=None,
        )
        for (cat_slug, topic_slug, date, edition) in prelim
    ]

    grouped = _group_and_pick_latest(temp_items, max_per_category, settings.category_list)

    # 拉取前 N 的文件标题与来源
    finalized: List[NavItem] = []
    for cat_slug, lst in grouped:
        for it in lst:
            title, source = _fetch_title_and_source(client, it.relpath, ref.default_branch)
            finalized.append(
                NavItem(
                    category_slug=cat_slug,
                    relpath=it.relpath,
                    title=title or it.title,
                    date=it.date,
                    edition=it.edition,
                    source_url=source,
                )
            )

    # 渲染
    lines: List[str] = [NAV_TITLE, ""]
    # 分类顺序同 settings, 通过 grouped 已保持
    for cat_slug, lst in grouped:
        display = slug_to_display.get(cat_slug, cat_slug)
        lines.append(f"## {display}")
        for it in [x for x in finalized if x.category_slug == cat_slug]:
            src = f" [来源]({it.source_url})" if it.source_url else ""
            lines.append(f"- [{it.title} - {it.date}]({it.relpath}) (v{it.edition}){src}")
        lines.append("")  # 空行分隔
    return "\n".join(lines).rstrip() + "\n"


def update_navigation_md(client: GitHubRepoClient, max_per_category: int = 20) -> None:
    nav_md = generate_navigation_md(client, max_per_category=max_per_category)
    client.ensure_file_updated(
        "NAVIGATION.md",
        nav_md,
        commit_message="chore: update NAVIGATION.md",
        branch=None,
    )


def _get_readme(client: GitHubRepoClient) -> str:
    data = client.get_contents("README.md")
    if not data:
        # 提供缺省 README
        return "# DeepResearch Archive\n\n<!-- BEGIN LATEST_REPORTS -->\n<!-- END LATEST_REPORTS -->\n"
    content_field = data.get("content")
    if not isinstance(content_field, str):
        return ""
    return _decode_content_field(content_field)


def _collect_latest_across_categories(items: List[NavItem], limit: int = 10) -> List[NavItem]:
    # 全局按日期与版次排序
    items_sorted = sorted(items, key=lambda x: (x.date, x.edition), reverse=True)
    return items_sorted[:limit]


def update_readme_latest_block(client: GitHubRepoClient, latest_limit: int = 10, max_per_category: int = 20) -> None:
    """
    构建最新报告区块并幂等写回 README。
    """
    ref = client.get_repo_ref()
    tree = client.list_tree(ref.tree_sha, recursive=True)

    prelim: List[Tuple[str, str, str, int]] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path.startswith(REPORT_PATH_PREFIX):
            continue
        parsed = _parse_report_path(path)
        if not parsed:
            continue
        prelim.append(parsed)

    temp_items: List[NavItem] = [
        NavItem(
            category_slug=cat_slug,
            relpath=to_posix(PurePosixPath("AI_Reports") / cat_slug / f"{topic_slug}-{date}--v{edition}.md"),
            title=topic_slug,
            date=date,
            edition=edition,
            source_url=None,
        )
        for (cat_slug, topic_slug, date, edition) in prelim
    ]

    # 只读取全局最新 limit 条的内容获取标题与来源, 降低 API 次数
    latest_candidates = _collect_latest_across_categories(temp_items, limit=latest_limit)
    detailed: List[NavItem] = []
    for it in latest_candidates:
        title, source = _fetch_title_and_source(client, it.relpath, ref.default_branch)
        detailed.append(
            NavItem(
                category_slug=it.category_slug,
                relpath=it.relpath,
                title=title or it.title,
                date=it.date,
                edition=it.edition,
                source_url=source,
            )
        )

    block_lines: List[str] = ["## 最新报告"]
    for it in detailed:
        src = f" [来源]({it.source_url})" if it.source_url else ""
        block_lines.append(f"- [{it.title} - {it.date}]({it.relpath}) (v{it.edition}){src}")
    block = "\n".join(block_lines)

    readme = _get_readme(client)
    new_readme = replace_block(readme, "LATEST_REPORTS", block)

    client.ensure_file_updated(
        "README.md",
        new_readme,
        commit_message="chore: update README latest block",
        branch=None,
    )