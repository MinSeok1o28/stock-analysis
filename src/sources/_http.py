"""경계 계층 공용 유틸: 레이트리밋 + 캐시.

sources/ 모듈만 이걸 쓴다. core/ 는 이 파일의 존재를 몰라야 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from ..config import ensure_loaded

ensure_loaded()   # .env 를 환경변수로 올린다 (경계 계층 진입 시 1회)

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "data/cache"))
_last_call: dict[str, float] = {}


class SourceUnavailable(Exception):
    """키 미설정·네트워크 실패 등. 호출자는 Unavailable로 변환해 전파한다."""


def throttle(host: str, min_interval: float) -> None:
    """SEC는 10 req/s 초과 시 429·IP 차단. 지키지 않으면 조용히 막힌다."""
    prev = _last_call.get(host)
    if prev is not None:
        wait = min_interval - (time.monotonic() - prev)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    cache_key: str | None = None,
    ttl_sec: int = 86_400,
    min_interval: float = 0.12,
    timeout: int = 30,
) -> Any:
    host = url.split("/")[2]
    key = cache_key or hashlib.sha256(
        (url + json.dumps(params or {}, sort_keys=True)).encode()
    ).hexdigest()[:24]
    cached = CACHE_DIR / host / f"{key}.json"

    if cached.exists() and (time.time() - cached.stat().st_mtime) < ttl_sec:
        return json.loads(cached.read_text(encoding="utf-8"))

    throttle(host, min_interval)
    try:
        resp = requests.get(url, headers=headers or {}, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        if cached.exists():   # 만료된 캐시라도 없는 것보다 낫다 — 단 호출자에 알린다
            return json.loads(cached.read_text(encoding="utf-8"))
        raise SourceUnavailable(f"{host}: {exc}") from exc

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cache_path: Path | None = None,
    ttl_sec: int = 31_536_000,
    min_interval: float = 0.12,
    timeout: int = 60,
) -> str:
    """대용량 문서(공시 원문 등)를 받아 캐시한다.

    JSON 이 아니므로 get_json 을 쓸 수 없다. 공시는 확정 문서라 사실상 불변이므로
    기본 TTL 을 1년으로 잡는다. cache_path 를 주면 data/raw 아래에 원문을 보존한다
    (인용 검증의 근거 — 나중에 페이지를 다시 확인할 수 있어야 한다).
    """
    host = url.split("/")[2]
    dest = cache_path or (CACHE_DIR / host /
                          (hashlib.sha256(url.encode()).hexdigest()[:24] + ".txt"))
    if dest.exists() and (time.time() - dest.stat().st_mtime) < ttl_sec:
        return dest.read_text(encoding="utf-8", errors="replace")
    throttle(host, min_interval)
    try:
        resp = requests.get(url, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        if dest.exists():
            return dest.read_text(encoding="utf-8", errors="replace")
        raise SourceUnavailable(f"{host}: {exc}") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(resp.text, encoding="utf-8")
    return resp.text


def require_env(var: str, hint: str) -> str:
    val = os.environ.get(var, "").strip()
    if not val:
        raise SourceUnavailable(f"{var} 미설정 — {hint}")
    return val
