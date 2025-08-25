# Daily DeepResearch - 库 A 配置加载
import os
from functools import lru_cache
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

# 加载 .env（若存在）
load_dotenv(override=True)

DEFAULT_CATEGORIES = [
    "人工智能和机器学习",
    "大型语言模型",
    "软件开发与工程",
    "网络安全",
    "云和 DevOps",
    "数据和数据库",
    "网络和移动",
    "消费电子和硬件",
    "游戏与互动",
    "区块链与加密",
    "科学与太空",
    "医疗保健与生物技术",
    "能源与气候",
    "经济与市场",
    "政策与法规",
    "行业与公司",
    "文化与媒体",
    "未分类",
]


class AppSettings(BaseModel):
    repo_b: str
    deepresearch_base_url: str  # DeepResearch 引擎 BaseURL（必填）
    tz: str = "Asia/Shanghai"
    repo_b_token: Optional[str] = None

    # AI 分类配置
    classify_with_ai: bool = False
    classifier_kind: str = "gemini"  # gemini | openai_compat | service
    classifier_base_url: Optional[str] = None  # gemini 默认 https://generativelanguage.googleapis.com；openai_compat 需提供 OpenAI 兼容 BaseURL；service 为自建 /classify
    classifier_model: Optional[str] = "gemini-2.0-flash"
    # 留空时将自动回退：gemini → 使用环境变量 GEMINI_API_KEY；openai_compat → 使用环境变量 OPENAI_API_KEY
    classifier_token: Optional[str] = None

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

    @field_validator("deepresearch_base_url", "classifier_base_url", mode="before")
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
        "deepresearch_base_url": _getenv("DEEPRESEARCH_BASE_URL"),
        "tz": _getenv("TZ", "Asia/Shanghai"),
        "repo_b_token": _getenv("REPO_B_TOKEN"),

        "category_list": _getenv("CATEGORY_LIST"),
        "url_whitelist": _getenv("URL_WHITELIST"),

        # AI 分类配置
        "classify_with_ai": (_getenv("CLASSIFY_WITH_AI", "false") or "false").lower() in {"1", "true", "yes", "on"},
        "classifier_kind": (_getenv("CLASSIFIER_KIND", "gemini") or "gemini").strip().lower(),
        "classifier_base_url": _getenv("CLASSIFIER_BASE_URL"),
        "classifier_model": _getenv("CLASSIFIER_MODEL", "gemini-2.0-flash"),
        "classifier_token": _getenv("CLASSIFIER_TOKEN"),

        # 运行期参数
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
    if not settings.deepresearch_base_url:
        missing.append("DEEPRESEARCH_BASE_URL")
    if not settings.tz:
        missing.append("TZ")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return _build_settings()


__all__ = ["AppSettings", "get_settings", "DEFAULT_CATEGORIES"]