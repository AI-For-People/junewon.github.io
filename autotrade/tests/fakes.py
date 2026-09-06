"""네트워크 없이 브로커 어댑터를 검증하기 위한 가짜 HTTP 세션."""

from __future__ import annotations

import json


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """URL 조각 → 응답 매핑. 호출 내역을 전부 기록한다."""

    def __init__(self, routes: dict[str, FakeResponse] | None = None):
        self.routes = routes or {}
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}

    def _match(self, url: str) -> FakeResponse:
        for fragment, resp in self.routes.items():
            if fragment in url:
                return resp
        raise AssertionError(f"준비되지 않은 요청: {url} (routes={list(self.routes)})")

    def request(self, method, url, **kw):
        self.calls.append({"method": method.upper(), "url": url, **kw})
        return self._match(url)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def close(self):
        pass

    def call_for(self, fragment: str) -> dict:
        for call in self.calls:
            if fragment in call["url"]:
                return call
        raise AssertionError(f"{fragment} 호출 기록 없음")
