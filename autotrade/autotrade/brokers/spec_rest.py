"""스펙(JSON) 기반 범용 REST 브로커 어댑터.

증권사마다 엔드포인트 경로와 응답 필드명만 다를 뿐 구조는 대동소이하다.
그래서 "코드"가 아니라 "매핑 파일"로 증권사를 붙일 수 있게 만든다.
아직 공개 문서가 없는 증권사(예: 메리츠증권)의 API가 출시되면,
JSON 한 장만 채우면 엔진 전체가 그대로 동작한다.

스펙 파일 구조는 config/broker_spec.schema.md 를 참고할 것.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ..config import Settings
from ..errors import AuthError, BrokerError, ConfigError
from ..journal import redact
from ..models import (
    AccountSnapshot,
    Candle,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
)
from .base import Broker

_PLACEHOLDER = re.compile(r"\$\{([A-Z_0-9]+)\}")


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """${KEY} 자리표시자를 ctx 값으로 치환한다(재귀)."""
    if isinstance(value, str):
        # 문자열 전체가 하나의 자리표시자면 원본 타입을 보존한다.
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return ctx.get(whole.group(1), "")
        return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), "")), value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def dig(data: Any, path: str | None, default: Any = None) -> Any:
    """'output.list[0].price' 형태의 경로로 중첩 응답에서 값을 꺼낸다."""
    if not path:
        return data
    cur = data
    for token in path.split("."):
        if not token:
            continue
        m = re.fullmatch(r"([^\[\]]*)(?:\[(\d+)\])?", token)
        if not m:
            return default
        key, idx = m.group(1), m.group(2)
        if key:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        if idx is not None:
            if not isinstance(cur, list) or len(cur) <= int(idx):
                return default
            cur = cur[int(idx)]
    return cur


def to_float(value: Any) -> float:
    if value in (None, "", []):
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


class BrokerSpec:
    """증권사 API 매핑 스펙."""

    def __init__(self, data: dict, source: Path | str = "<inline>"):
        self.data = data
        self.source = str(source)
        self.name = data.get("name", "unknown")
        self.base_url = (data.get("base_url") or "").rstrip("/")
        self.auth = data.get("auth") or {}
        self.endpoints = data.get("endpoints") or {}
        self.rate_limit_per_sec = float(data.get("rate_limit_per_sec", 2))

    @classmethod
    def load(cls, path: Path | str) -> "BrokerSpec":
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"브로커 스펙 파일이 없습니다: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"브로커 스펙 JSON 파싱 실패({p}): {exc}") from exc
        return cls(data, p)

    def unresolved_todos(self) -> list[str]:
        """아직 채우지 않은 TODO 자리를 찾아낸다."""
        todos: list[str] = []

        def walk(node: Any, trail: str) -> None:
            if isinstance(node, str) and "TODO" in node:
                todos.append(f"{trail} = {node}")
            elif isinstance(node, dict):
                for k, v in node.items():
                    if str(k).startswith("_"):
                        continue  # _readme 등 주석용 키는 검사 대상이 아니다
                    walk(v, f"{trail}.{k}" if trail else k)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{trail}[{i}]")

        walk(self.data, "")
        if not self.base_url or "TODO" in self.base_url:
            todos.append("base_url")
        return todos

    def endpoint(self, key: str) -> dict:
        ep = self.endpoints.get(key)
        if not ep:
            raise BrokerError(
                f"[{self.name}] 스펙에 '{key}' 엔드포인트가 정의되지 않았습니다 ({self.source})."
            )
        return ep


class SpecRestBroker(Broker):
    """스펙 파일이 정의한 대로 REST를 호출하는 브로커."""

    def __init__(
        self,
        settings: Settings,
        spec: BrokerSpec,
        *,
        journal=None,
        session: requests.Session | None = None,
        credentials: dict[str, str] | None = None,
    ):
        self.settings = settings
        self.spec = spec
        self.name = spec.name
        self.journal = journal
        self.session = session or requests.Session()
        self.timeout = 10.0
        self._token: str | None = None
        self._token_expires: datetime | None = None
        self._last_call = 0.0
        self.credentials = credentials or {}

        missing = spec.unresolved_todos()
        if missing:
            raise ConfigError(
                f"[{spec.name}] API 스펙이 아직 완성되지 않았습니다 ({spec.source}).\n"
                "증권사 공식 API 문서를 받은 뒤 아래 항목을 채우십시오:\n  - "
                + "\n  - ".join(missing[:20])
            )

    # ------------------------------------------------------------- 공통
    def _ctx(self, **extra: Any) -> dict[str, Any]:
        ctx = {
            "APP_KEY": self.credentials.get("app_key", ""),
            "APP_SECRET": self.credentials.get("app_secret", ""),
            "ACCOUNT_NO": self.credentials.get("account_no", ""),
            "ACCOUNT_PD": self.credentials.get("account_pd", ""),
            "TOKEN": self._token or "",
        }
        ctx.update({k: v for k, v in extra.items()})
        return ctx

    def _throttle(self) -> None:
        gap = 1.0 / max(0.1, self.spec.rate_limit_per_sec)
        wait = gap - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def access_token(self) -> str:
        if self._token and self._token_expires and self._token_expires > datetime.now(
            timezone.utc
        ) + timedelta(minutes=5):
            return self._token
        auth = self.spec.auth
        if not auth:
            return ""
        ctx = self._ctx()
        self._throttle()
        resp = self.session.request(
            auth.get("method", "POST"),
            f"{self.spec.base_url}{auth['path']}",
            json=render(auth.get("body", {}), ctx),
            headers=render(auth.get("headers", {"content-type": "application/json"}), ctx),
            timeout=self.timeout,
        )
        data = _safe_json(resp)
        token = dig(data, auth.get("token_field", "access_token"))
        if not token:
            raise AuthError(
                f"[{self.name}] 접근토큰 발급 실패 (HTTP {resp.status_code})",
                payload=redact(data),
            )
        expires_in = int(to_float(dig(data, auth.get("expires_field", "expires_in"))) or 3600)
        self._token = str(token)
        self._token_expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return self._token

    def _call(self, key: str, ctx_extra: dict[str, Any]) -> Any:
        ep = self.spec.endpoint(key)
        if self.spec.auth:
            self.access_token()
        ctx = self._ctx(**ctx_extra)
        headers = render(self.spec.auth.get("header_template", {}), ctx)
        headers.update(render(ep.get("headers", {}), ctx))
        method = ep.get("method", "GET").upper()
        url = f"{self.spec.base_url}{render(ep['path'], ctx)}"
        self._throttle()
        if method == "GET":
            resp = self.session.get(
                url, headers=headers, params=render(ep.get("params", {}), ctx), timeout=self.timeout
            )
        else:
            resp = self.session.request(
                method,
                url,
                headers=headers,
                json=render(ep.get("body", {}), ctx),
                timeout=self.timeout,
            )
        data = _safe_json(resp)
        if resp.status_code >= 400:
            raise BrokerError(
                f"[{self.name}] {key} 호출 실패 (HTTP {resp.status_code})",
                code=str(resp.status_code),
                payload=redact(data),
            )
        # 업무 오류 코드 검사(증권사마다 다르므로 스펙에 선언한다)
        ok = ep.get("success_check") or self.spec.data.get("success_check")
        if ok:
            actual = dig(data, ok["field"])
            if str(actual) != str(ok["equals"]):
                raise BrokerError(
                    f"[{self.name}] {key} 업무 오류: "
                    f"{dig(data, ok.get('message_field', ''), '')} ({ok['field']}={actual})",
                    payload=redact(data),
                )
        return data

    # ------------------------------------------------------------- 시세
    def get_quote(self, symbol: str) -> Quote:
        ep = self.spec.endpoint("quote")
        data = self._call("quote", {"SYMBOL": symbol})
        fields = ep.get("response", {})
        price = to_float(dig(data, fields.get("price")))
        if not price:
            raise BrokerError(f"[{self.name}] {symbol} 현재가 파싱 실패", payload=redact(data))
        return Quote(
            symbol=symbol,
            price=price,
            bid=to_float(dig(data, fields.get("bid"))) or None,
            ask=to_float(dig(data, fields.get("ask"))) or None,
        )

    def get_candles(self, symbol: str, count: int = 100) -> list[Candle]:
        ep = self.spec.endpoint("candles")
        end = datetime.now()
        start = end - timedelta(days=max(count * 2, 30))
        data = self._call(
            "candles",
            {
                "SYMBOL": symbol,
                "DATE_FROM": start.strftime("%Y%m%d"),
                "DATE_TO": end.strftime("%Y%m%d"),
                "COUNT": str(count),
            },
        )
        fields = ep.get("response", {})
        rows = dig(data, fields.get("list")) or []
        date_fmt = fields.get("date_format", "%Y%m%d")
        candles: list[Candle] = []
        for row in rows:
            close = to_float(dig(row, fields.get("close")))
            raw_date = dig(row, fields.get("date"))
            if not close or not raw_date:
                continue
            try:
                ts = datetime.strptime(str(raw_date), date_fmt)
            except ValueError:
                continue
            candles.append(
                Candle(
                    ts=ts,
                    open=to_float(dig(row, fields.get("open"))) or close,
                    high=to_float(dig(row, fields.get("high"))) or close,
                    low=to_float(dig(row, fields.get("low"))) or close,
                    close=close,
                    volume=to_float(dig(row, fields.get("volume"))),
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles[-count:]

    # ------------------------------------------------------------- 계좌
    def get_account(self) -> AccountSnapshot:
        ep = self.spec.endpoint("balance")
        data = self._call("balance", {})
        fields = ep.get("response", {})
        positions: dict[str, Position] = {}
        for row in dig(data, fields.get("positions")) or []:
            symbol = str(dig(row, fields.get("position_symbol")) or "").strip()
            qty = int(to_float(dig(row, fields.get("position_qty"))))
            if not symbol or qty <= 0:
                continue
            positions[symbol] = Position(
                symbol=symbol,
                qty=qty,
                avg_price=to_float(dig(row, fields.get("position_avg_price"))),
            )
        return AccountSnapshot(cash=to_float(dig(data, fields.get("cash"))), positions=positions)

    # ------------------------------------------------------------- 주문
    def submit_order(self, order: Order) -> Order:
        ep = self.spec.endpoint("order")
        codes = ep.get("codes", {})
        is_market = order.order_type is OrderType.MARKET
        ctx = {
            "SYMBOL": order.symbol,
            "QTY": str(int(order.qty)),
            "PRICE": "0" if is_market else str(int(round(order.price or 0))),
            "SIDE": codes.get("sell" if order.side is Side.SELL else "buy", order.side.value),
            "ORDER_TYPE": codes.get("market" if is_market else "limit", order.order_type.value),
            "CLIENT_ORDER_ID": order.client_order_id,
        }
        try:
            data = self._call("order", ctx)
        except BrokerError as exc:
            order.status = OrderStatus.REJECTED
            order.note = str(exc)
            if self.journal:
                self.journal.write("order.rejected", order=order.to_dict(), error=str(exc))
            return order
        fields = ep.get("response", {})
        order.broker_order_id = str(dig(data, fields.get("order_id")) or "").strip() or None
        order.status = OrderStatus.SUBMITTED
        order.note = str(dig(data, fields.get("message"), "") or "")
        order.updated_at = datetime.now()
        if self.journal:
            self.journal.write("order.submitted", order=order.to_dict(), broker=self.name)
        return order

    def close(self) -> None:
        self.session.close()


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}
