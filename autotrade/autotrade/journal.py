"""감사 로그(append-only JSONL) + 콘솔 로깅.

자동매매에서 "무슨 근거로 언제 무엇을 주문했는가"를 재구성할 수 없으면
사고가 났을 때 원인 규명도, 증권사·세무 대응도 불가능하다. 모든 판단을 남긴다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("autotrade")

# 로그에 절대 남기면 안 되는 키
_SECRET_KEYS = re.compile(
    r"(appkey|appsecret|app_key|app_secret|access_token|approval_key|authorization|secret|password|hashkey)",
    re.IGNORECASE,
)
_ACCOUNT_RE = re.compile(r"\b(\d{4})\d{4}\b")


def redact(value: Any) -> Any:
    """비밀값을 재귀적으로 마스킹한다."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = "***REDACTED***" if _SECRET_KEYS.search(str(k)) else redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _ACCOUNT_RE.sub(r"\1****", value)
    return value


class Journal:
    """JSONL 감사 로그. 한 줄 = 한 사건."""

    def __init__(self, path: Path | str, *, echo: bool = True):
        self.path = Path(path)
        self.echo = echo
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **payload: Any) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **redact(payload),
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        # append-only: 기존 내용을 절대 덮어쓰지 않는다.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if self.echo:
            LOG.info("%s %s", event, json.dumps(redact(payload), ensure_ascii=False, default=str))
        return record

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
