"""한국투자증권 KIS Developers REST API 어댑터.

왜 KIS인가
---------
국내 증권사 중 (a) 개인에게 공개된 공식 REST/WebSocket API를 제공하고,
(b) Windows/ActiveX(OCX) 없이 리눅스·서버에서 동작하며,
(c) 모의투자 서버가 별도로 있어 실계좌 전 검증이 가능한 사실상 유일한 선택지다.
(키움 OpenAPI+/영웅문은 Windows COM, LS증권 XingAPI도 Windows DLL 기반이다.
 다만 각 사가 신규 REST API를 내놓고 있으니 도입 시점에 재확인할 것.)

⚠️ TR_ID / 엔드포인트 경로는 KIS가 개정한다. 아래 값은 작성 시점 기준의 기본값이며
   반드시 KIS 개발자센터 최신 문서로 대조하고, 다르면 환경변수로 덮어써라.
   (KIS_TRID_BUY / KIS_TRID_SELL / KIS_TRID_BALANCE 등)

⚠️ 접근토큰은 발급 횟수 제한이 있다. 반드시 파일 캐시를 재사용하고,
   프로세스를 재시작할 때마다 새로 발급받지 않도록 한다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ..config import Settings
from ..errors import AuthError, BrokerError
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

# --------------------------------------------------------------- 엔드포인트
PATH_TOKEN = "/oauth2/tokenP"
PATH_REVOKE = "/oauth2/revokeP"
PATH_APPROVAL = "/oauth2/Approval"
PATH_HASHKEY = "/uapi/hashkey"
PATH_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
PATH_DAILY = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
PATH_ORDER_CASH = "/uapi/domestic-stock/v1/trading/order-cash"
PATH_ORDER_RVSECNCL = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
PATH_BALANCE = "/uapi/domestic-stock/v1/trading/inquire-balance"

# TR_ID 기본값 (실전/모의). 환경변수로 덮어쓸 수 있다.
TRID = {
    "price": "FHKST01010100",          # 주식현재가 시세 (실전/모의 공통)
    "daily": "FHKST03010100",          # 국내주식 기간별 시세(일/주/월/년)
    "buy":     {"live": "TTTC0802U", "paper": "VTTC0802U"},
    "sell":    {"live": "TTTC0801U", "paper": "VTTC0801U"},
    "cancel":  {"live": "TTTC0803U", "paper": "VTTC0803U"},
    "balance": {"live": "TTTC8434R", "paper": "VTTC8434R"},
}

# ORD_DVSN(주문구분)
ORD_DVSN_LIMIT = "00"   # 지정가
ORD_DVSN_MARKET = "01"  # 시장가


class RateLimiter:
    """초당 호출 수 제한(토큰 버킷).

    KIS는 계정 등급/서버에 따라 유량 제한이 다르다(모의투자가 훨씬 빡빡하다).
    한도를 넘기면 일시 차단되므로 기본값을 보수적으로 잡는다.
    """

    def __init__(self, max_calls: int, per_seconds: float = 1.0):
        self.max_calls = max(1, max_calls)
        self.per = per_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] > self.per:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self.per - (now - self._calls[0]) + 0.01
            time.sleep(max(0.01, sleep_for))


class TokenStore:
    """접근토큰 파일 캐시. 퍼미션 0600으로 저장한다."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self, key_fingerprint: str) -> tuple[str, datetime] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if data.get("fingerprint") != key_fingerprint:
            return None
        try:
            expires = datetime.fromisoformat(data["expires_at"])
        except (KeyError, ValueError):
            return None
        # 만료 5분 전이면 새로 받는다.
        if expires - timedelta(minutes=5) <= datetime.now(timezone.utc):
            return None
        return data["access_token"], expires

    def save(self, token: str, expires_at: datetime, key_fingerprint: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": token,
            "expires_at": expires_at.isoformat(),
            "fingerprint": key_fingerprint,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)


class KisBroker(Broker):
    name = "kis"

    def __init__(self, settings: Settings, *, journal=None, session: requests.Session | None = None):
        self.settings = settings
        self.journal = journal
        self.base_url = settings.kis_base_url
        self.is_live = settings.is_live
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "autotrade/0.1", "charset": "UTF-8"})
        self.tokens = TokenStore(settings.token_cache_path)
        # 모의투자는 유량 제한이 훨씬 엄격하다.
        default_rate = 8 if self.is_live else 2
        self.limiter = RateLimiter(int(os.environ.get("KIS_MAX_CALLS_PER_SEC", default_rate)))
        self._access_token: str | None = None
        self._token_expires: datetime | None = None
        self.timeout = float(os.environ.get("KIS_HTTP_TIMEOUT", "10"))

    # --------------------------------------------------------------- 내부
    @property
    def _fingerprint(self) -> str:
        import hashlib

        raw = f"{self.settings.kis_app_key}:{self.base_url}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def _trid(self, kind: str) -> str:
        env_key = f"KIS_TRID_{kind.upper()}"
        override = os.environ.get(env_key)
        if override:
            return override.strip()
        entry = TRID[kind]
        if isinstance(entry, str):
            return entry
        return entry["live" if self.is_live else "paper"]

    def access_token(self) -> str:
        if self._access_token and self._token_expires and self._token_expires > datetime.now(
            timezone.utc
        ) + timedelta(minutes=5):
            return self._access_token

        cached = self.tokens.load(self._fingerprint)
        if cached:
            self._access_token, self._token_expires = cached
            return self._access_token

        self.limiter.acquire()
        resp = self.session.post(
            f"{self.base_url}{PATH_TOKEN}",
            json={
                "grant_type": "client_credentials",
                "appkey": self.settings.kis_app_key,
                "appsecret": self.settings.kis_app_secret,
            },
            headers={"content-type": "application/json"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(
                f"접근토큰 발급 실패 (HTTP {resp.status_code}). "
                "앱키/시크릿과 실전·모의 서버 선택을 확인하십시오.",
                code=str(resp.status_code),
                payload=redact(_safe_json(resp)),
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise AuthError("응답에 access_token 이 없습니다.", payload=redact(data))
        expires_in = int(data.get("expires_in", 60 * 60 * 6))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        self._access_token, self._token_expires = token, expires_at
        self.tokens.save(token, expires_at, self._fingerprint)
        if self.journal:
            self.journal.write("kis.token_issued", expires_at=expires_at.isoformat())
        return token

    def approval_key(self) -> str:
        """실시간 시세 WebSocket 접속용 approval_key."""
        self.limiter.acquire()
        resp = self.session.post(
            f"{self.base_url}{PATH_APPROVAL}",
            json={
                "grant_type": "client_credentials",
                "appkey": self.settings.kis_app_key,
                "secretkey": self.settings.kis_app_secret,
            },
            headers={"content-type": "application/json"},
            timeout=self.timeout,
        )
        data = _safe_json(resp)
        key = (data or {}).get("approval_key")
        if not key:
            raise AuthError("approval_key 발급 실패", payload=redact(data))
        return key

    def _headers(self, tr_id: str, *, hashkey: str | None = None) -> dict[str, str]:
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token()}",
            "appkey": self.settings.kis_app_key,
            "appsecret": self.settings.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    def _hashkey(self, body: dict) -> str:
        """주문 등 POST 바디 위변조 방지용 해시."""
        self.limiter.acquire()
        resp = self.session.post(
            f"{self.base_url}{PATH_HASHKEY}",
            json=body,
            headers={
                "content-type": "application/json; charset=utf-8",
                "appkey": self.settings.kis_app_key,
                "appsecret": self.settings.kis_app_secret,
            },
            timeout=self.timeout,
        )
        data = _safe_json(resp) or {}
        key = data.get("HASH")
        if not key:
            raise BrokerError("hashkey 발급 실패", payload=redact(data))
        return key

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        self.limiter.acquire()
        resp = self.session.get(
            f"{self.base_url}{path}",
            headers=self._headers(tr_id),
            params=params,
            timeout=self.timeout,
        )
        return self._unwrap(resp, path)

    def _post(self, path: str, tr_id: str, body: dict, *, use_hashkey: bool = True) -> dict:
        hashkey = self._hashkey(body) if use_hashkey else None
        self.limiter.acquire()
        resp = self.session.post(
            f"{self.base_url}{path}",
            headers=self._headers(tr_id, hashkey=hashkey),
            data=json.dumps(body),
            timeout=self.timeout,
        )
        return self._unwrap(resp, path)

    def _unwrap(self, resp: requests.Response, path: str) -> dict:
        data = _safe_json(resp)
        if resp.status_code != 200 or data is None:
            raise BrokerError(
                f"{path} 호출 실패 (HTTP {resp.status_code})",
                code=str(resp.status_code),
                payload=redact(data),
            )
        # KIS 규약: rt_cd == "0" 이면 정상, 그 외는 msg1에 사유가 담긴다.
        rt_cd = data.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            raise BrokerError(
                f"{path} 업무 오류: {data.get('msg1', '').strip()} (msg_cd={data.get('msg_cd')})",
                code=data.get("msg_cd"),
                payload=redact(data),
            )
        return data

    # --------------------------------------------------------------- 시세
    def get_quote(self, symbol: str) -> Quote:
        data = self._get(
            PATH_PRICE,
            self._trid("price"),
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        out = data.get("output") or {}
        price = _to_float(out.get("stck_prpr"))
        if not price:
            raise BrokerError(f"{symbol} 현재가를 파싱하지 못했습니다.", payload=redact(out))
        return Quote(
            symbol=symbol,
            price=price,
            bid=_to_float(out.get("stck_sdpr")) or None,
            ask=_to_float(out.get("stck_sdpr")) or None,
        )

    def get_candles(self, symbol: str, count: int = 100) -> list[Candle]:
        end = datetime.now()
        start = end - timedelta(days=max(count * 2, 30))
        data = self._get(
            PATH_DAILY,
            self._trid("daily"),
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",  # 일봉
                "FID_ORG_ADJ_PRC": "0",      # 0=수정주가 반영
            },
        )
        rows = data.get("output2") or []
        candles: list[Candle] = []
        for row in rows:
            day = row.get("stck_bsop_date")
            close = _to_float(row.get("stck_clpr"))
            if not day or not close:
                continue
            candles.append(
                Candle(
                    ts=datetime.strptime(day, "%Y%m%d"),
                    open=_to_float(row.get("stck_oprc")) or close,
                    high=_to_float(row.get("stck_hgpr")) or close,
                    low=_to_float(row.get("stck_lwpr")) or close,
                    close=close,
                    volume=_to_float(row.get("acml_vol")) or 0.0,
                )
            )
        candles.sort(key=lambda c: c.ts)   # KIS는 최신순으로 준다
        return candles[-count:]

    # --------------------------------------------------------------- 계좌
    def get_account(self) -> AccountSnapshot:
        data = self._get(
            PATH_BALANCE,
            self._trid("balance"),
            {
                "CANO": self.settings.kis_account_no,
                "ACNT_PRDT_CD": self.settings.kis_account_pd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        positions: dict[str, Position] = {}
        for row in data.get("output1") or []:
            symbol = (row.get("pdno") or "").strip()
            qty = int(_to_float(row.get("hldg_qty")) or 0)
            if not symbol or qty <= 0:
                continue
            positions[symbol] = Position(
                symbol=symbol,
                qty=qty,
                avg_price=_to_float(row.get("pchs_avg_pric")) or 0.0,
            )
        summary = (data.get("output2") or [{}])[0]
        # dnca_tot_amt: 예수금 총액 / ord_psbl_cash: 주문가능현금
        cash = _to_float(summary.get("ord_psbl_cash")) or _to_float(summary.get("dnca_tot_amt")) or 0.0
        return AccountSnapshot(cash=cash, positions=positions)

    # --------------------------------------------------------------- 주문
    def submit_order(self, order: Order) -> Order:
        if order.side is Side.SELL and not self.is_live:
            pass  # 모의투자도 매도 TR은 동일 계열을 쓴다
        tr_id = self._trid("sell" if order.side is Side.SELL else "buy")
        is_market = order.order_type is OrderType.MARKET
        body = {
            "CANO": self.settings.kis_account_no,
            "ACNT_PRDT_CD": self.settings.kis_account_pd,
            "PDNO": order.symbol,
            "ORD_DVSN": ORD_DVSN_MARKET if is_market else ORD_DVSN_LIMIT,
            "ORD_QTY": str(int(order.qty)),
            # 시장가는 단가 0으로 보낸다.
            "ORD_UNPR": "0" if is_market else str(int(round(order.price or 0))),
        }
        try:
            data = self._post(PATH_ORDER_CASH, tr_id, body)
        except BrokerError as exc:
            order.status = OrderStatus.REJECTED
            order.note = str(exc)
            order.updated_at = datetime.now()
            if self.journal:
                self.journal.write("order.rejected", order=order.to_dict(), error=str(exc))
            return order

        out = data.get("output") or {}
        order.broker_order_id = (out.get("ODNO") or "").strip() or None
        order.status = OrderStatus.SUBMITTED
        order.note = (data.get("msg1") or "").strip()
        order.updated_at = datetime.now()
        if self.journal:
            self.journal.write(
                "order.submitted",
                order=order.to_dict(),
                krx_fwdg_ord_orgno=out.get("KRX_FWDG_ORD_ORGNO"),
                ord_tmd=out.get("ORD_TMD"),
            )
        return order

    def cancel_order(self, order: Order) -> Order:
        if not order.broker_order_id:
            raise BrokerError("취소하려면 broker_order_id 가 필요합니다.")
        body = {
            "CANO": self.settings.kis_account_no,
            "ACNT_PRDT_CD": self.settings.kis_account_pd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order.broker_order_id,
            "ORD_DVSN": ORD_DVSN_LIMIT,
            "RVSE_CNCL_DVSN_CD": "02",  # 02 = 취소
            "ORD_QTY": str(int(order.remaining_qty or order.qty)),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        self._post(PATH_ORDER_RVSECNCL, self._trid("cancel"), body)
        order.status = OrderStatus.CANCELED
        order.updated_at = datetime.now()
        return order

    def close(self) -> None:
        self.session.close()


def _safe_json(resp: requests.Response) -> dict | None:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0
