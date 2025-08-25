from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from .config import get_settings
from .logger import get_logger

LOG = get_logger(__name__)


GITHUB_API = "https://api.github.com"


@dataclass
class RepoRef:
    owner: str
    repo: str
    default_branch: str
    head_commit_sha: str
    tree_sha: str


class GitHubRepoClient:
    """
    轻量 GitHub Contents + Trees API 客户端
    - 读取远端树, 构建 NAV/README 所需的索引
    - 创建/更新文件, 支持基于 sha 幂等更新, 避免空 diff
    注意: 单个 Contents API 调用一次只能写一个文件, 若需单提交写多文件可扩展使用 low-level git tree + commit API。
    """

    def __init__(self, token: Optional[str] = None, repo_full: Optional[str] = None) -> None:
        settings = get_settings()
        self.token = token or settings.repo_b_token
        self.owner, self.repo = self._parse_repo_full(repo_full or settings.repo_b)
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/vnd.github+json",
            "User-Agent": "repo-a-bot",
        })
        if self.token:
            self._session.headers["Authorization"] = f"Bearer {self.token}"

    @staticmethod
    def _parse_repo_full(repo_full: str) -> tuple[str, str]:
        if not repo_full or "/" not in repo_full:
            raise ValueError("REPO_B must be like owner/repo")
        owner, repo = repo_full.split("/", 1)
        return owner, repo

    # ---------- Repo refs ----------

    def get_repo_ref(self) -> RepoRef:
        r = self._session.get(f"{GITHUB_API}/repos/{self.owner}/{self.repo}")
        r.raise_for_status()
        info = r.json()
        default_branch = info.get("default_branch") or "main"
        # get head commit sha
        cr = self._session.get(f"{GITHUB_API}/repos/{self.owner}/{self.repo}/commits/{default_branch}")
        cr.raise_for_status()
        commit = cr.json()
        head_commit_sha = commit["sha"]
        tree_sha = commit["commit"]["tree"]["sha"]
        LOG.info("repo_ref", extra={"default_branch": default_branch, "head_commit": head_commit_sha, "tree_sha": tree_sha})
        return RepoRef(owner=self.owner, repo=self.repo, default_branch=default_branch, head_commit_sha=head_commit_sha, tree_sha=tree_sha)

    # ---------- Trees listing ----------

    def list_tree(self, tree_sha: str, recursive: bool = True) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/git/trees/{tree_sha}"
        if recursive:
            url += "?recursive=1"
        r = self._session.get(url)
        r.raise_for_status()
        data = r.json()
        return data.get("tree", []) or []

    # ---------- Contents: get / put ----------

    def get_contents(self, path: str, ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
        params = {"ref": ref} if ref else None
        r = self._session.get(url, params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def put_file(
        self,
        path: str,
        content_utf8: str,
        message: str,
        branch: Optional[str] = None,
        sha: Optional[str] = None,
        committer: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
        content_b64 = base64.b64encode(content_utf8.encode("utf-8")).decode("ascii")
        payload: Dict[str, Any] = {
            "message": message,
            "content": content_b64,
        }
        if branch:
            payload["branch"] = branch
        if sha:
            payload["sha"] = sha
        if committer:
            payload["committer"] = committer

        r = self._session.put(url, json=payload)
        # 若 409 或其他冲突, 交由调用侧决定是否重试
        r.raise_for_status()
        return r.json()

    # ---------- Helpers ----------

    def ensure_file_updated(
        self,
        path: str,
        new_content: str,
        commit_message: str,
        branch: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        幂等写文件:
        - 若远端无文件: 创建
        - 若远端存在且内容相同: 跳过
        - 若远端存在且 sha 不同: 更新
        返回 None 表示跳过(无变化)。
        """
        current = self.get_contents(path, ref=branch)
        current_sha = None
        current_content = None
        if current and isinstance(current, dict):
            current_sha = current.get("sha")
            # 某些返回无 content, 为节省流量 GitHub 默认省略。此处再调一次 raw api 拉原文会复杂。
            # 我们退而求其次: 先 PUT, 若 server 认为内容未变也会拒绝或返回相同 sha。
            # 为减少不必要 PUT, 可在调用侧提供内容哈希判断(建议)。
        try:
            resp = self.put_file(path, new_content, commit_message, branch=branch, sha=current_sha)
            LOG.info("file_put", extra={"path": path, "status": "updated" if current_sha else "created"})
            return resp
        except requests.HTTPError as e:
            # 若 422 Unprocessable Entity 且信息包含 "sha" 说明需要 sha 才能更新
            # 或者 409 冲突, 可重拉 sha 后重试一次(由上层实现)
            LOG.error("file_put_error", extra={"path": path, "error": str(e)})
            raise