from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple, DefaultDict, Set
from collections import defaultdict

from slugify import slugify
try:
    from pypinyin import lazy_pinyin, Style
except ImportError:
    # 如果没有安装 pypinyin，提供一个简单的回退函数
    def lazy_pinyin(text, style=None):
        return [text]  # 直接返回原文
    
    class Style:
        FIRST_LETTER = None

from .config import get_settings
from .utils import replace_block, to_posix, today_str
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


def _get_pinyin_first_letter(text: str) -> str:
    """
    获取文本的拼音首字母，用于排序和分组
    """
    if not text:
        return "#"
    
    first_char = text[0]
    
    # 如果是数字，归入 0-9 组
    if first_char.isdigit():
        return "0-9"
    
    # 如果是英文字母，直接返回大写
    if first_char.isalpha() and ord(first_char) < 128:
        return first_char.upper()
    
    # 如果是中文或其他字符，转换为拼音首字母
    try:
        pinyin_list = lazy_pinyin(first_char, style=Style.FIRST_LETTER)
        if pinyin_list and pinyin_list[0]:
            return pinyin_list[0].upper()
    except Exception:
        pass
    
    # 其他情况归入 # 组
    return "#"


def _get_pinyin_sort_key(text: str) -> str:
    """
    获取文本的拼音排序键，用于字典序排序
    """
    if not text:
        return ""
    
    try:
        # 将整个文本转换为拼音
        pinyin_list = lazy_pinyin(text, style=Style.TONE3)
        return "".join(pinyin_list).lower()
    except Exception:
        # 如果转换失败，返回原文的小写
        return text.lower()


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
        # 提供新的 README 模板，按照用户要求
        return """# DeepResearch — Today's Reports

本页仅展示"当日最新报告"。历史与按分类的完整导航请见 NAVIGATION.md。此页内容由 GemFlow 自动生成并每日更新。

**GemFlow3** 是一个自动化每日 DeepResearch 的工作流系统，集成了热榜抓取、主题分类、AI 生成报告、内容归档与导航更新等功能，旨在实现研究内容的自动化生产与结构化展示。

## 文档导航

- **README.md**（本页）：展示今日最新报告
- **NAVIGATION.md**：按分类浏览历史报告（每类最多20条，按日期倒序）
- **AI_Reports/[类别]/Reports.md**：各类别完整报告索引（按标题首字母排序）

---

使用说明:
- 请勿手工修改 TODAY_REPORTS 与 DATE 标记之间的内容，本项目会幂等替换。
- 历史导航：参见 NAVIGATION.md（按分类分组，日期倒序，最近 20 条）。
- 完整索引：各类别的 Reports.md 包含该类别所有历史报告。
- 报告存放路径：AI_Reports/<category_slug>/<title>-<date>--v<edition>.md

---

相关文档:
- NAVIGATION.md
- PROJECT_OVERVIEW.md

---

<!-- BEGIN TODAY_REPORTS -->
<!-- END TODAY_REPORTS -->
"""
    content_field = data.get("content")
    if not isinstance(content_field, str):
        return ""
    return _decode_content_field(content_field)


def _collect_latest_across_categories(items: List[NavItem], limit: int = 10) -> List[NavItem]:
    # 全局按日期与版次排序
    items_sorted = sorted(items, key=lambda x: (x.date, x.edition), reverse=True)
    return items_sorted[:limit]


def _collect_today_reports(items: List[NavItem]) -> List[NavItem]:
    """
    只收集今日的报告
    """
    today = today_str()
    today_items = [item for item in items if item.date == today]
    # 按版次倒序排序（同一天内最新版本优先）
    return sorted(today_items, key=lambda x: x.edition, reverse=True)


def update_readme_latest_block(client: GitHubRepoClient, latest_limit: int = 10, max_per_category: int = 20) -> None:
    """
    构建今日报告区块并幂等写回 README。
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

    # 只读取今日报告的内容获取标题与来源
    today_candidates = _collect_today_reports(temp_items)
    detailed: List[NavItem] = []
    for it in today_candidates:
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

    if detailed:
        block_lines: List[str] = ["## 最新报告"]
        for it in detailed:
            src = f" [来源]({it.source_url})" if it.source_url else ""
            block_lines.append(f"- [{it.title} - {it.date}]({it.relpath}) (v{it.edition}){src}")
        block = "\n".join(block_lines)
    else:
        # 如果今日没有报告，显示提示信息
        block = "## 最新报告\n今日暂无报告。"

    readme = _get_readme(client)
    new_readme = replace_block(readme, "TODAY_REPORTS", block)

    client.ensure_file_updated(
        "README.md",
        new_readme,
        commit_message="chore: update README latest block",
        branch=None,
    )


def _collect_category_reports(items: List[NavItem], category_slug: str) -> List[NavItem]:
    """
    收集指定类别的所有报告
    """
    category_items = [item for item in items if item.category_slug == category_slug]
    # 按标题拼音字典序排序（首字母相同时按第二字母排序，依此类推）
    return sorted(category_items, key=lambda x: _get_pinyin_sort_key(x.title))


def generate_category_reports_md(client: GitHubRepoClient, category_slug: str) -> str:
    """
    生成指定类别的Reports.md内容，包含该类别所有报告，按拼音首字母字典序排序
    """
    ref = client.get_repo_ref()
    tree = client.list_tree(ref.tree_sha, recursive=True)

    # 收集指定类别的所有报告
    category_reports: List[Tuple[str, str, str, int]] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path.startswith(REPORT_PATH_PREFIX):
            continue
        parsed = _parse_report_path(path)
        if not parsed:
            continue
        cat_slug, topic_slug, date_str, edition = parsed
        if cat_slug == category_slug:
            category_reports.append((cat_slug, topic_slug, date_str, edition))

    if not category_reports:
        return f"# {category_slug} 报告索引\n\n暂无报告。\n"

    # 构造NavItem并获取详细信息
    temp_items: List[NavItem] = [
        NavItem(
            category_slug=cat_slug,
            relpath=to_posix(PurePosixPath("AI_Reports") / cat_slug / f"{topic_slug}-{date}--v{edition}.md"),
            title=topic_slug,  # 先用 slug, 稍后替换为文件内"主题"
            date=date,
            edition=edition,
            source_url=None,
        )
        for (cat_slug, topic_slug, date, edition) in category_reports
    ]

    # 获取报告的实际标题和来源
    detailed_reports: List[NavItem] = []
    for item in temp_items:
        title, source = _fetch_title_and_source(client, item.relpath, ref.default_branch)
        detailed_reports.append(
            NavItem(
                category_slug=item.category_slug,
                relpath=item.relpath,
                title=title or item.title,
                date=item.date,
                edition=item.edition,
                source_url=source,
            )
        )

    # 按标题字典序排序
    sorted_reports = _collect_category_reports(detailed_reports, category_slug)

    # 获取分类的显示名称
    settings = get_settings()
    slug_to_display = _slug_display_map(settings.category_list)
    display_name = slug_to_display.get(category_slug, category_slug)

    # 生成Markdown内容
    lines: List[str] = [
        f"# {display_name} 报告索引",
        "",
        f"本页包含 **{display_name}** 类别下的所有报告，按拼音首字母字典序排序。",
        f"报告总数：{len(sorted_reports)}",
        "",
        "---",
        "",
    ]

    current_letter = ""
    for report in sorted_reports:
        # 获取标题拼音首字母
        letter = _get_pinyin_first_letter(report.title)

        # 如果是新的字母分组，添加标题
        if letter != current_letter:
            if current_letter:  # 不是第一个分组时添加空行
                lines.append("")
            lines.append(f"## {letter}")
            lines.append("")
            current_letter = letter

        # 构建相对路径（去掉 AI_Reports/ 前缀）
        relative_path = report.relpath
        if relative_path.startswith("AI_Reports/"):
            relative_path = relative_path[11:]  # 去掉 "AI_Reports/" 前缀

        # 添加报告条目
        src = f" [来源]({report.source_url})" if report.source_url else ""
        lines.append(f"- [{report.title}]({relative_path}) - {report.date} (v{report.edition}){src}")

    return "\n".join(lines) + "\n"


def update_category_reports_md(client: GitHubRepoClient, category_slug: str) -> None:
    """
    更新指定类别的Reports.md文件
    """
    reports_md_content = generate_category_reports_md(client, category_slug)
    reports_md_path = f"AI_Reports/{category_slug}/Reports.md"
    
    client.ensure_file_updated(
        reports_md_path,
        reports_md_content,
        commit_message=f"chore: update {category_slug} Reports.md",
        branch=None,
    )


def update_all_category_reports_md(client: GitHubRepoClient) -> None:
    """
    更新所有类别的Reports.md文件
    """
    ref = client.get_repo_ref()
    tree = client.list_tree(ref.tree_sha, recursive=True)

    # 收集所有存在的类别
    existing_categories: Set[str] = set()
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path.startswith(REPORT_PATH_PREFIX):
            continue
        parsed = _parse_report_path(path)
        if parsed:
            existing_categories.add(parsed[0])

    # 为每个类别更新Reports.md
    for category_slug in existing_categories:
        try:
            update_category_reports_md(client, category_slug)
            LOG.info("category_reports_updated", extra={"category": category_slug})
        except Exception as e:
            LOG.error("category_reports_update_failed", extra={"category": category_slug, "error": repr(e)})
