import os
import sys
import uuid
import logging
from typing import Any, Dict
from datetime import datetime
from zoneinfo import ZoneInfo

import orjson

# 全局 run_id, 贯穿一次运行便于追踪
_RUN_ID = os.environ.get("RUN_ID") or uuid.uuid4().hex[:12]
os.environ["RUN_ID"] = _RUN_ID


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class JsonlFormatter(logging.Formatter):
    """
    将日志序列化为 jsonl, 带本地时区时间戳与 run_id。
    额外字段通过 logging.Logger.info("msg", extra={"key": "val"}) 注入。
    """

    def __init__(self, tz: str = "Asia/Shanghai") -> None:
        super().__init__()
        self.tz = tz

    def format(self, record: logging.LogRecord) -> str:
        tz = ZoneInfo(self.tz)
        payload: Dict[str, Any] = {
            "ts": datetime.now(tz).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": _RUN_ID,
            "msg": record.getMessage(),
        }

        # 合并额外字段
        std_keys = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
        }
        for k, v in record.__dict__.items():
            if k not in std_keys and k not in payload and not k.startswith("_"):
                try:
                    # 尝试直接序列化
                    orjson.dumps(v)
                    payload[k] = v
                except TypeError:
                    payload[k] = repr(v)

        if record.exc_info:
            try:
                payload["exc_info"] = self.formatException(record.exc_info)
            except Exception:  # noqa: BLE001
                payload["exc_info"] = "unavailable"

        return orjson.dumps(payload).decode("utf-8")


def get_run_id() -> str:
    return _RUN_ID


def get_logger(
    name: str = "Flow",
    tz: str = "Asia/Shanghai",
    log_dir: str = os.path.join("Flow", "state", "logs"),
) -> logging.Logger:
    """
    获取带 JSONL 输出的 logger, 同时写入 stdout 与文件 Flow/state/logs/YYYY-MM-DD.jsonl
    幂等初始化: 重复调用不会重复添加 handler
    """
    logger = logging.getLogger(name)
    if getattr(logger, "_initialized", False):
        return logger

    logger.setLevel(logging.INFO)
    _ensure_dir(log_dir)

    formatter = JsonlFormatter(tz=tz)

    # stdout
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    # file
    date_str = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
    file_path = os.path.join(log_dir, f"{date_str}.jsonl")
    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger._initialized = True  # type: ignore[attr-defined]
    logger.info("logger_initialized", extra={"event": "logger_initialized", "run_id": _RUN_ID})
    return logger


__all__ = ["get_logger", "get_run_id", "JsonlFormatter"]