# Daily DeepResearch - 库 A 配置加载
import os
from functools import lru_cache
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

# 加载 .env（若存在）
load_dotenv(override=True)

DEFAULT_CATEGORIES = ["AI", "安全", "开源", "芯片", "云与大数据", "其他"]


class AppSettings(BaseModel):
    repo_b: str
    api_base_url: str
    tz: str = "Asia/Shanghai"
    repo_b_token: Optional[str] = None

    # 分类集合
    category_list: List[str] = Field(default_factory=lambda: DEFAULT_CATEGORIES.copy())

    # URL 白名单（域名列表，逗号分隔），用于内容安全过滤
    url_whitelist: List[str] = Field(default_factory=list)

    # 运行期参数
    max_concurrent_topics: int = 3
    http_max_retries: int = 3
    http_backoff_seconds: float = 3.0
    max_reports_per_run: int = 5

    @field_validator("category_list", mode="before")
    @classmethod
    def parse_categories(cls, v):
        if v is None or v == "":
            return DEFAULT_CATEGORIES.copy()
        if isinstance(v, str):
            parts = [s.strip() for s in v.split(",") if s.strip()]
            return parts or DEFAULT_CATEGORIES.copy()
        if isinstance(v, list):
            return v
        raise TypeError("CATEGORY_LIST must be a comma separated string or list")

    @field_validator("url_whitelist", mode="before")
    @classmethod
    def parse_whitelist(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            parts = [s.strip().lower() for s in v.split(",") if s.strip()]
            return parts
        if isinstance(v, list):
            return [str(x).strip().lower() for x in v]
        raise TypeError("URL_WHITELIST must be a comma separated string or list")

    @field_validator("api_base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, v):
        if isinstance(v, str):
            return v.rstrip("/")
        return v


def _getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


def _build_settings() -> AppSettings:
    data = {
        "repo_b": _getenv("REPO_B"),
        "api_base_url": _getenv("API_BASE_URL"),
        "tz": _getenv("TZ", "Asia/Shanghai"),
        "repo_b_token": _getenv("REPO_B_TOKEN"),

        "category_list": _getenv("CATEGORY_LIST"),
        "url_whitelist": _getenv("URL_WHITELIST"),

        "max_concurrent_topics": int(_getenv("MAX_CONCURRENT_TOPICS", "3")),
        "http_max_retries": int(_getenv("HTTP_MAX_RETRIES", "3")),
        "http_backoff_seconds": float(_getenv("HTTP_BACKOFF_SECONDS", "3")),
        "max_reports_per_run": int(_getenv("MAX_REPORTS_PER_RUN", "5")),
    }
    settings = AppSettings.model_validate(data)
    _assert_required(settings)
    return settings


def _assert_required(settings: AppSettings) -> None:
    missing = []
    if not settings.repo_b:
        missing.append("REPO_B")
    if not settings.api_base_url:
        missing.append("API_BASE_URL")
    if not settings.tz:
        missing.append("TZ")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return _build_settings()


__all__ = ["AppSettings", "get_settings", "DEFAULT_CATEGORIES"]