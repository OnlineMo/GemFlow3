from __future__ import annotations

import base64
import os
import sys
from typing import Optional

from src.config import get_settings
from src.logger import get_logger, get_run_id
from src.trends import fetch_trends_cached
from src.topics import extract_candidates_via_ai, classify_topic
from src.history import next_available_edition, record_status
from src.engine_client import deepresearch_generate_markdown
from src.github_api import GitHubRepoClient
from src.renderers import update_navigation_md, update_readme_latest_block
from src.utils import report_relpath, to_posix, today_str, ensure_utf8


def _decode_base64(text_b64: Optional[str]) -> str:
    if not isinstance(text_b64, str):
        return ""
    try:
        raw = base64.b64decode(text_b64.encode("ascii"))
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _ensure_file_if_changed(client: GitHubRepoClient, path: str, new_content: str, commit_message: str) -> Optional[dict]:
    """
    先 GET 远端文件并解码对比内容，若一致则跳过 PUT，避免空 diff 提交。
    """
    current = client.get_contents(path)
    if current and isinstance(current, dict):
        content_field = current.get("content")
        if isinstance(content_field, str):
            old = _decode_base64(content_field)
            if old == new_content:
                # 无变更
                return None
    return client.ensure_file_updated(path, new_content, commit_message, branch=None)


def main() -> int:
    settings = get_settings()
    LOG = get_logger(tz=settings.tz)
    run_id = get_run_id()

    # 在 CI 中推荐注入 REPO_B_TOKEN；本地可用 .env 或手动导出
    token_present = bool(os.environ.get("REPO_B_TOKEN") or settings.repo_b_token)
    dry_run = os.environ.get("DRY_RUN", "").strip() in {"1", "true", "yes"}

    if dry_run:
        LOG.info("mode_dry_run", extra={"dry_run": True})
    elif not token_present:
        LOG.info("token_missing_run_readonly", extra={"note": "REPO_B_TOKEN not found, skip push to repo B"})

    date = today_str(settings.tz)
    LOG.info("workflow_start", extra={"run_id": run_id, "date": date})

    # 1) 拉取热榜
    trends = fetch_trends_cached(date_str=date)

    # 2) 提取候选主题
    candidates = extract_candidates_via_ai(trends)
    if not candidates:
        LOG.info("no_candidates_today", extra={"date": date})
        return 0

    # 限制每次运行的最大报告数量
    candidates = candidates[: max(1, settings.max_reports_per_run)]

    client = GitHubRepoClient() if (not dry_run and token_present) else None

    ok_cnt = 0
    fail_cnt = 0
    skipped_cnt = 0

    for cand in candidates:
        topic = cand.topic
        category, cat_conf = classify_topic(topic)
        edition = next_available_edition(topic, date, start=cand.edition_hint)

        relpath = to_posix(report_relpath(category, topic, date, edition))
        status_fp = record_status(
            topic=topic,
            category=category,
            date=date,
            edition=edition,
            relpath=relpath,
            status="pending",
            run_id=run_id,
        )

        LOG.info(
            "processing_topic",
            extra={
                "topic": topic,
                "category": category,
                "edition": edition,
                "relpath": relpath,
                "fingerprint": status_fp,
            },
        )

        try:
            # 3) 调用 DeepResearch 引擎生成 Markdown
            result = deepresearch_generate_markdown(
                topic=topic,
                category=category,
                source_url=cand.sources[0] if cand.sources else None,
                edition=f"v{edition}",
                configurable={
                    "number_of_initial_queries": 3,
                    "max_research_loops": 2,
                    # 可按需注入其他图参数
                },
            )
            md_text = ensure_utf8(result.content or "").strip()
            if not md_text:
                raise RuntimeError("empty_markdown_generated")

            if dry_run or client is None:
                LOG.info(
                    "dry_run_generated",
                    extra={"topic": topic, "category": category, "len": len(md_text)},
                )
                record_status(
                    topic=topic,
                    category=category,
                    date=date,
                    edition=edition,
                    relpath=relpath,
                    status="ok",
                    run_id=run_id,
                )
                ok_cnt += 1
                continue

            # 4) 写入库 B
            commit_message = f"feat: add report {topic} - {date} v{edition} run {run_id}"
            resp = _ensure_file_if_changed(client, relpath, md_text, commit_message)
            if resp is None:
                LOG.info("no_change_skip_commit", extra={"path": relpath})
                # 认为已存在同内容，也视为成功
                record_status(
                    topic=topic,
                    category=category,
                    date=date,
                    edition=edition,
                    relpath=relpath,
                    status="ok",
                    run_id=run_id,
                )
                skipped_cnt += 1
            else:
                record_status(
                    topic=topic,
                    category=category,
                    date=date,
                    edition=edition,
                    relpath=relpath,
                    status="ok",
                    run_id=run_id,
                )
                ok_cnt += 1

        except Exception as e:
            LOG.error(
                "topic_failed",
                extra={"topic": topic, "category": category, "edition": edition, "error": repr(e)},
            )
            record_status(
                topic=topic,
                category=category,
                date=date,
                edition=edition,
                relpath=relpath,
                status="failed",
                run_id=run_id,
            )
            fail_cnt += 1
            continue

    # 5) 更新导航与 README（仅在非 dry-run 且有 token 时）
    if not dry_run and client is not None:
        try:
            update_navigation_md(client, max_per_category=20)
            update_readme_latest_block(client, latest_limit=10, max_per_category=20)
        except Exception as e:
            # 导航/README 更新失败不影响单篇报告状态
            LOG.error("nav_readme_update_failed", extra={"error": repr(e)})

    LOG.info(
        "workflow_done",
        extra={"ok": ok_cnt, "failed": fail_cnt, "skipped": skipped_cnt, "run_id": run_id},
    )
    return 0 if (ok_cnt > 0 or fail_cnt == 0) else 1


if __name__ == "__main__":
    sys.exit(main())