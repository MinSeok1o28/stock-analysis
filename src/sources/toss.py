"""2차 출처: 토스증권 Open API — 시세·종목 정보만.

토스증권은 거래소 시세를 재배포하는 규제 브로커이므로 Tier.VENDOR(2차)다.
수치 사용이 허용된다. (발행 주체는 거래소이므로 1차는 아니다.)

## 안전 경계 — CLAUDE.md 개정 조항의 구현체

이 모듈은 주문을 낼 수 없다. 세 겹으로 막는다:
  1. GET만 보낸다. `_get()` 외의 요청 함수가 없다.
  2. `ALLOWED_PATHS` 화이트리스트. 밖의 경로는 호출 즉시 예외.
  3. **`X-Tossinvest-Account` 헤더를 만드는 코드가 없다.** 토스는 계좌·자산·주문 API에
     이 헤더를 요구하므로, 헤더가 없으면 그 API들은 애초에 동작하지 않는다.

`tests/test_layering.py`가 위 세 가지를 검사한다.
액세스 토큰은 메모리에만 둔다 (디스크에 비밀을 쓰지 않는다).

## 실측 응답 스키마 (2026-08-24 확인)
    /prices        {"result": [{"symbol","timestamp","lastPrice","currency"}]}
    /candles       {"result": {"candles": [{"timestamp","openPrice","highPrice",
                                            "lowPrice","closePrice","volume","currency"}],
                               "nextBefore": "..."}}
    /exchange-rate {"result": {"rate","midRate","validFrom","validUntil",...}}
  · 모든 수치가 **문자열**로 온다 → _num() 으로 변환한다.
  · 캔들은 **최신순**으로 온다 → daily_candles() 가 시간순으로 뒤집어 반환한다.
  · /prices 에 전일대비 필드가 없다 → 변동률은 캔들이 필요하다 (종목당 1콜).

## 필요한 설정
토스증권 WTS > 설정 > Open API 에서 client_id / client_secret 발급 후 .env 에:
    TOSS_CLIENT_ID=...
    TOSS_CLIENT_SECRET=...
같은 화면 하단 **허용 IP 관리**에 이 머신의 공인 IP를 등록해야 한다.
미등록 IP는 403이며, 이는 자격증명이 유출돼도 타 IP에서 쓸 수 없게 하는 보호 장치다.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime

import requests

from ..provenance import Sourced, Unavailable, vendor_api
from ._http import SourceUnavailable, get_json, require_env

BASE = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
NAME = "토스증권 Open API"

# 화이트리스트. 시세·종목·시장정보만. 계좌(/accounts,/holdings)·주문(/orders)은 의도적으로 없다.
ALLOWED_PATHS: frozenset[str] = frozenset({
    "/api/v1/prices",
    "/api/v1/candles",
    "/api/v1/orderbook",
    "/api/v1/trades",
    "/api/v1/price-limits",
    "/api/v1/stocks",
    "/api/v1/exchange-rate",
    "/api/v1/market-calendar/KR",
    "/api/v1/market-calendar/US",
    "/api/v1/market-indicators/prices",
    "/api/v1/rankings",
    "/api/v1/stocks/all",
})

#: 경로 파라미터가 있는 허용 패턴. 접두사 + 접미사로만 매칭한다 (주문 경로는 걸리지 않는다).
ALLOWED_PATTERNS: tuple[tuple[str, str], ...] = (
    ("/api/v1/market-indicators/", "/candles"),
)

# 계좌 헤더 이름을 문자열로도 두지 않는다 — grep으로 검증 가능하게.
_FORBIDDEN_HEADER_HINT = "계좌 헤더는 이 모듈에서 생성하지 않는다"

# 그룹별 TPS (overview.md 기준). 여유를 두고 간격을 잡는다.
_INTERVAL = {
    "/api/v1/candles": 0.06,               # MARKET_DATA_CHART 20/s
    "/api/v1/market-indicators/prices": 0.11,  # 10/s
    "/api/v1/stocks": 0.21,                # STOCK 5/s
    "/api/v1/stocks/all": 1.1,             # STOCK_ALL 1/s
    "/api/v1/exchange-rate": 0.34,         # MARKET_INFO 3/s
}
_DEFAULT_INTERVAL = 0.07                    # MARKET_DATA 15/s

_token: tuple[str, float] | None = None     # (access_token, 만료 epoch). 메모리 전용.


class OrderPathBlocked(Exception):
    """허용 경로 밖 호출. 주문·계좌 API를 막는 마지막 방어선."""


def _access_token() -> str:
    global _token
    if _token and time.time() < _token[1] - 30:
        return _token[0]
    cid = require_env("TOSS_CLIENT_ID", "토스증권 WTS > 설정 > Open API 에서 발급")
    sec = require_env("TOSS_CLIENT_SECRET", "토스증권 WTS > 설정 > Open API 에서 발급")
    try:
        r = requests.post(
            BASE + TOKEN_PATH,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec},
            timeout=20,
        )
        if r.status_code == 403:
            raise SourceUnavailable(
                "403 — 허용 IP 미등록으로 보인다. 토스증권 WTS > 설정 > Open API > "
                "허용 IP 관리에 현재 공인 IP를 등록하라 (curl -s ifconfig.me 로 확인)"
            )
        r.raise_for_status()
        p = r.json()
    except requests.RequestException as exc:
        raise SourceUnavailable(f"{NAME} 토큰 발급 실패: {exc}") from exc
    tok = p.get("access_token")
    if not tok:
        raise SourceUnavailable(f"{NAME} 토큰 응답에 access_token 없음")
    _token = (tok, time.time() + float(p.get("expires_in", 1800)))
    return tok


def _get(path: str, params: dict | None = None, *, ttl_sec: int = 60):
    """GET 전용. 화이트리스트 밖은 호출하지 않는다. 계좌 헤더를 넣지 않는다."""
    if path not in ALLOWED_PATHS and not any(
            path.startswith(a) and path.endswith(b) for a, b in ALLOWED_PATTERNS):
        raise OrderPathBlocked(
            f"{path} 는 허용 경로가 아니다. 이 모듈은 시세·종목 정보만 조회한다 "
            "(CLAUDE.md 운영 원칙)."
        )
    headers = {"Authorization": f"Bearer {_access_token()}"}
    return get_json(BASE + path, headers=headers, params=params,
                    ttl_sec=ttl_sec, min_interval=_INTERVAL.get(path, _DEFAULT_INTERVAL))


def _url(path: str, params: dict) -> str:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{BASE}{path}?{q}"


def _pick(node, *keys):
    """응답 스키마 변화에 견디도록 후보 키를 순서대로 시도한다."""
    if isinstance(node, dict):
        for k in keys:
            if k in node and node[k] is not None:
                return node[k]
    return None


def _num(v) -> float | None:
    """토스는 수치를 문자열로 준다. 조용히 0으로 만들지 않고 None으로 실패를 드러낸다."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _unwrap(payload):
    """{"result": ...} 봉투를 벗긴다."""
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _rows(payload, *, nested_key: str | None = None):
    node = _unwrap(payload)
    if nested_key and isinstance(node, dict):
        node = node.get(nested_key, node)
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        return [node]
    return []


# ── 공개 함수 ────────────────────────────────────────────────────────────

def last_close(ticker: str) -> Sourced[float] | Unavailable:
    """단일 종목 현재가. 다건은 prices()를 쓸 것 (한 번에 200종목)."""
    got = prices([ticker])
    if isinstance(got, Unavailable):
        return got
    v = got.get(ticker.upper())
    return v if v is not None else Unavailable(f"{ticker} 현재가", "응답에 해당 심볼 없음")


def prices(tickers: list[str]) -> dict[str, Sourced[float]] | Unavailable:
    """현재가 다건. 최대 200종목을 한 번의 호출로 받는다 — 포트폴리오 전체가 1콜."""
    if not tickers:
        return {}
    if len(tickers) > 200:
        return Unavailable("현재가 다건", f"200종목 초과({len(tickers)}) — 분할 호출 필요")
    syms = ",".join(t.upper() for t in tickers)
    params = {"symbols": syms}
    try:
        payload = _get("/api/v1/prices", params, ttl_sec=60)
    except SourceUnavailable as exc:
        return Unavailable("현재가", f"{NAME}: {exc}")
    src = vendor_api(f"{NAME} 현재가", _url("/api/v1/prices", params))
    out: dict[str, Sourced[float]] = {}
    for row in _rows(payload):
        sym = _pick(row, "symbol", "code", "ticker")
        px = _num(_pick(row, "lastPrice", "closePrice", "close", "price", "currentPrice"))
        if sym and px is not None:
            out[str(sym).upper()] = Sourced(px, src)
    return out or Unavailable("현재가", f"{NAME} 응답 파싱 실패 — 스키마 변경 확인 필요")


def daily_candles(ticker: str, count: int = 200, *, adjusted: bool = True
                  ) -> Sourced[list[dict]] | Unavailable:
    """일봉 OHLCV. 수정주가 기본 적용 — 배당·분할 조정된 값이어야 성장률 비교가 성립한다.

    최대 200봉 ≈ 10개월. 밤사이 움직임·이상치·기술적 맥락에 충분하다.

    반환은 **시간순(오래된 것 → 최신)** 으로 정규화한다. API는 최신순으로 주는데,
    그대로 쓰면 변동률 부호가 뒤집힌다. 필드도 open/high/low/close/volume 으로 통일한다.
    마지막 봉은 장중이면 미완성 봉일 수 있다 (거래량으로 판별 가능).
    """
    params = {"symbol": ticker.upper(), "interval": "1d",
              "count": min(count, 200), "adjusted": str(adjusted).lower()}
    try:
        payload = _get("/api/v1/candles", params, ttl_sec=3_600)
    except SourceUnavailable as exc:
        return Unavailable(f"{ticker} 일봉", f"{NAME}: {exc}")
    raw = _rows(payload, nested_key="candles")
    if not raw:
        return Unavailable(f"{ticker} 일봉", f"{NAME} 응답 비어 있음")
    norm = []
    for r in raw:
        ts = _pick(r, "timestamp", "time", "date")
        c = _num(_pick(r, "closePrice", "close", "c"))
        if ts is None or c is None:
            continue
        norm.append({
            "timestamp": ts,
            "open": _num(_pick(r, "openPrice", "open")),
            "high": _num(_pick(r, "highPrice", "high")),
            "low": _num(_pick(r, "lowPrice", "low")),
            "close": c,
            "volume": _num(_pick(r, "volume")),
            "currency": _pick(r, "currency"),
        })
    if not norm:
        return Unavailable(f"{ticker} 일봉", f"{NAME} 캔들 필드 파싱 실패 — 스키마 확인 필요")
    norm.sort(key=lambda x: str(x["timestamp"]))   # 시간순으로 정규화
    return Sourced(norm, vendor_api(
        f"{NAME} 일봉{'(수정주가)' if adjusted else ''}", _url("/api/v1/candles", params)))


def daily_candles_paged(ticker: str, pages: int = 3, *, adjusted: bool = True
                        ) -> Sourced[list[dict]] | Unavailable:
    """일봉을 여러 페이지 이어붙인다. 1페이지 200봉 ≈ 10개월, 3페이지면 약 2년.

    52주 신고가·신저가와 다분기 이벤트 반응 통계에 필요하다.
    nextBefore 로 페이지네이션한다.
    """
    rows: list[dict] = []
    before: str | None = None
    src = None
    for _ in range(max(1, pages)):
        params = {"symbol": ticker.upper(), "interval": "1d",
                  "count": 200, "adjusted": str(adjusted).lower()}
        if before:
            params["before"] = before
        try:
            payload = _get("/api/v1/candles", params, ttl_sec=43_200)
        except SourceUnavailable as exc:
            if rows:
                break
            return Unavailable(f"{ticker} 일봉", f"{NAME}: {exc}")
        node = _unwrap(payload)
        chunk = node.get("candles", []) if isinstance(node, dict) else []
        if not chunk:
            break
        rows += chunk
        src = src or vendor_api(f"{NAME} 일봉(수정주가, {pages}페이지)",
                                _url("/api/v1/candles", params))
        before = node.get("nextBefore") if isinstance(node, dict) else None
        if not before:
            break
    norm = {}
    for r in rows:
        ts = _pick(r, "timestamp", "time", "date")
        c = _num(_pick(r, "closePrice", "close"))
        if ts is None or c is None:
            continue
        norm[str(ts)[:10]] = {
            "date": str(ts)[:10],
            "open": _num(_pick(r, "openPrice", "open")),
            "high": _num(_pick(r, "highPrice", "high")),
            "low": _num(_pick(r, "lowPrice", "low")),
            "close": c,
            "volume": _num(_pick(r, "volume")) or 0.0,
        }
    if not norm:
        return Unavailable(f"{ticker} 일봉", f"{NAME} 캔들 파싱 실패")
    return Sourced([norm[k] for k in sorted(norm)], src)


def overnight_move(ticker: str) -> Sourced[float] | Unavailable:
    """직전 2개 일봉의 종가 변동률. 브리핑의 '밤사이 움직임' 신호 입력."""
    c = daily_candles(ticker, count=2)
    if isinstance(c, Unavailable):
        return c
    rows = c.value                      # daily_candles 가 시간순으로 정규화해 둔다
    if len(rows) < 2:
        return Unavailable(f"{ticker} 변동률", "일봉 2개 미만")
    prev, cur = rows[-2]["close"], rows[-1]["close"]
    if not prev:
        return Unavailable(f"{ticker} 변동률", "직전 종가 0")
    return Sourced((cur - prev) / prev, c.source)


def exchange_rate(base: str = "USD", quote: str = "KRW",
                  at: datetime | None = None) -> Sourced[float] | Unavailable:
    """실제 적용 환율. 시점 지정이 가능해 과거 스냅샷 재현에 쓸 수 있다.

    Frankfurter(ECB 영업일 종가)와 달리 장중 값이다. 콕핏 평가금액에는 이쪽이 맞고,
    매크로 추세에는 Frankfurter가 낫다 — 둘을 함께 두는 이유.
    """
    params = {"baseCurrency": base, "quoteCurrency": quote}
    if at is not None:
        params["dateTime"] = at.isoformat(timespec="seconds")
    try:
        payload = _get("/api/v1/exchange-rate", params, ttl_sec=600)
    except SourceUnavailable as exc:
        return Unavailable(f"{base}/{quote} 환율", f"{NAME}: {exc}")
    node = _unwrap(payload)
    node = node if isinstance(node, dict) else {}
    v = _pick(node, "rate", "midRate", "exchangeRate", "value")
    n = _num(v)
    if n is None:
        return Unavailable(f"{base}/{quote} 환율", f"{NAME} 응답 파싱 실패")
    return Sourced(n, vendor_api(f"{NAME} 환율", _url("/api/v1/exchange-rate", params)))


def stock_info(tickers: list[str]) -> Sourced[dict[str, dict]] | Unavailable:
    """종목 기본 정보. 심볼로 키잉해서 반환한다. 최대 200건을 1콜로.

    주는 것: name(한글명) · englishName · market · securityType · currency ·
             sharesOutstanding · status · listDate
    종목명은 거의 안 바뀌므로 캐시를 길게 잡는다.
    """
    syms = [t.upper() for t in tickers if t]
    if not syms:
        return Sourced({}, vendor_api(f"{NAME} 종목정보", f"{BASE}/api/v1/stocks"))
    if len(syms) > 200:
        return Unavailable("종목 정보", f"200종목 초과({len(syms)}) — 분할 호출 필요")
    params = {"symbols": ",".join(syms)}
    try:
        payload = _get("/api/v1/stocks", params, ttl_sec=604_800)
    except SourceUnavailable as exc:
        return Unavailable("종목 정보", f"{NAME}: {exc}")
    out: dict[str, dict] = {}
    for r in _rows(payload):
        sym = _pick(r, "symbol")
        if not sym:
            continue
        out[str(sym).upper()] = {
            "name": _pick(r, "name") or "",
            "english_name": _pick(r, "englishName") or "",
            "market": _pick(r, "market") or "",
            "security_type": _pick(r, "securityType") or "",
            "currency": _pick(r, "currency") or "",
            "shares_outstanding": _num(_pick(r, "sharesOutstanding")),
            "status": _pick(r, "status") or "",
        }
    return (Sourced(out, vendor_api(f"{NAME} 종목정보", _url("/api/v1/stocks", params)))
            if out else Unavailable("종목 정보", f"{NAME} 응답 파싱 실패"))


#: 검색 자동완성용 마켓. KR_ETC·US_ETC 는 제외(잡음).
UNIVERSE_MARKETS = ("KOSPI", "KOSDAQ", "NASDAQ", "NYSE", "AMEX")


def universe(markets: tuple[str, ...] = UNIVERSE_MARKETS
             ) -> Sourced[list[dict]] | Unavailable:
    """마켓별 종목 마스터. 검색 자동완성의 재료.

    일 배치로 갱신되는 저변동 데이터라 캐시를 길게(1주) 잡는다.
    마켓당 수천 건이므로 보통주 STOCK 만 받는다.
    """
    out: list[dict] = []
    failed: list[str] = []
    for m in markets:
        params = {"market": m, "status": "ACTIVE", "securityType": "STOCK",
                  "commonShare": "true"}
        try:
            payload = _get("/api/v1/stocks/all", params, ttl_sec=604_800)
        except SourceUnavailable as exc:
            failed.append(f"{m}({exc})")
            continue
        for r in _rows(payload):
            sym = _pick(r, "symbol")
            if not sym:
                continue
            out.append({"symbol": str(sym).upper(),
                        "name": _pick(r, "name") or "",
                        "english_name": _pick(r, "englishName") or "",
                        "market": _pick(r, "market") or m})
    if not out:
        return Unavailable("종목 마스터", f"{NAME}: 전 마켓 실패 {failed}")
    src = vendor_api(f"{NAME} 종목 마스터 ({len(out):,}종목)",
                     f"{BASE}/api/v1/stocks/all")
    return Sourced(out, src)


def names(tickers: list[str]) -> dict[str, str]:
    """{심볼: 표시명}. 최선 노력 — 실패하면 빈 dict 를 돌려 호출부가 심볼만 쓰게 한다.

    한글명을 우선하되 없으면 영문명. 005930 → '삼성전자' 처럼 사람이 읽을 수 있게 만든다.
    """
    info = stock_info(tickers)
    if isinstance(info, Unavailable):
        return {}
    return {k: (v["name"] or v["english_name"] or k) for k, v in info.value.items()}


#: 국내 시장지표 심볼 (실측 확인). 카탈로그 밖은 400 unsupported-symbol.
KR_INDICATORS = ("KOSPI", "KOSDAQ")
#: 미국 지수는 시장지표 API 대상이 아니다 → 대표 ETF를 대용치로 쓰고 그 사실을 표기한다.
US_INDEX_PROXIES = {"SPY": "S&P 500", "QQQ": "나스닥 100", "DIA": "다우 30", "IWM": "러셀 2000"}


def market_indicators(symbols: list[str] | None = None) -> dict[str, Sourced[float]] | Unavailable:
    """국내 지수·국채 현재가. KOSPI/KOSDAQ 등 카탈로그 심볼만 지원한다."""
    syms = ",".join(symbols or KR_INDICATORS)
    params = {"symbols": syms}
    try:
        payload = _get("/api/v1/market-indicators/prices", params, ttl_sec=60)
    except SourceUnavailable as exc:
        return Unavailable("국내 시장지표", f"{NAME}: {exc}")
    src = vendor_api(f"{NAME} 시장지표", _url("/api/v1/market-indicators/prices", params))
    out = {}
    for row in _rows(payload):
        sym = _pick(row, "symbol")
        v = _num(_pick(row, "lastPrice", "price", "close"))
        if sym and v is not None:
            out[str(sym)] = Sourced(v, src)
    return out or Unavailable("국내 시장지표", f"{NAME} 응답 파싱 실패")


def indicator_move(symbol: str) -> Sourced[float] | Unavailable:
    """지수의 직전 대비 변동률. 시장지표 전용 캔들 엔드포인트를 쓴다."""
    path = f"/api/v1/market-indicators/{symbol}/candles"
    params = {"interval": "1d", "count": 2}
    try:
        payload = _get(path, params, ttl_sec=1_800)
    except (SourceUnavailable, OrderPathBlocked) as exc:
        return Unavailable(f"{symbol} 변동률", f"{NAME}: {exc}")
    rows = _rows(payload, nested_key="candles")
    closes = [(str(_pick(r, "timestamp", "time")), _num(_pick(r, "closePrice", "close")))
              for r in rows]
    closes = [(t, c) for t, c in closes if c is not None]
    if len(closes) < 2:
        return Unavailable(f"{symbol} 변동률", "캔들 2개 미만")
    closes.sort(key=lambda x: x[0])            # 최신순으로 오므로 시간순 정규화
    prev, cur = closes[-2][1], closes[-1][1]
    if not prev:
        return Unavailable(f"{symbol} 변동률", "직전 종가 0")
    return Sourced((cur - prev) / prev, vendor_api(f"{NAME} {symbol} 일봉", BASE + path))


RANKING_TYPES = ("MARKET_TRADING_AMOUNT", "TOP_GAINERS", "TOP_LOSERS",
                 "MARKET_TRADING_VOLUME")


def rankings(kind: str = "MARKET_TRADING_AMOUNT", market: str = "KR",
             duration: str = "1d", count: int = 10
             ) -> Sourced[list[dict]] | Unavailable:
    """시장 랭킹. 보유하지 않은 '주요 종목' 현황의 소스.

    응답에 `price.changeRate` 가 포함돼 있어 종목별 캔들 조회 없이 등락률을 얻는다.
    """
    params = {"type": kind, "marketCountry": market, "duration": duration,
              "count": min(count, 100)}
    try:
        payload = _get("/api/v1/rankings", params, ttl_sec=300)
    except SourceUnavailable as exc:
        return Unavailable(f"{market} {kind} 랭킹", f"{NAME}: {exc}")
    node = _unwrap(payload)
    rows = node.get("rankings", []) if isinstance(node, dict) else []
    out = []
    for r in rows:
        px = r.get("price") or {}
        last = _num(_pick(px, "lastPrice"))
        if last is None:
            continue
        out.append({
            "rank": r.get("rank"),
            "symbol": str(_pick(r, "symbol") or ""),
            "last": last,
            "base": _num(_pick(px, "basePrice")),
            "change_rate": _num(_pick(px, "changeRate")),
            "trading_amount": _num(_pick(r, "tradingAmount")),
            "currency": _pick(r, "currency"),
        })
    if not out:
        return Unavailable(f"{market} {kind} 랭킹", f"{NAME} 응답 파싱 실패")
    ranked_at = node.get("rankedAt", "") if isinstance(node, dict) else ""
    return Sourced(out, vendor_api(f"{NAME} {market} {kind}",
                                   _url("/api/v1/rankings", params),
                                   section=f"기준 {ranked_at[:19]}"))


def market_calendar(market: str = "US") -> Sourced[dict] | Unavailable:
    """장 운영 시간. 실적 발표 일정이 아니다 — 그건 이 API가 제공하지 않는다."""
    path = f"/api/v1/market-calendar/{market.upper()}"
    try:
        payload = _get(path, ttl_sec=86_400)
    except (SourceUnavailable, OrderPathBlocked) as exc:
        return Unavailable(f"{market} 장 운영정보", f"{NAME}: {exc}")
    return Sourced(payload, vendor_api(f"{NAME} 장운영({market})", BASE + path))


if __name__ == "__main__":
    import sys
    tks = sys.argv[1:] or ["AAPL", "MSFT"]
    print(f"── 현재가 {tks} ──")
    got = prices(tks)
    if isinstance(got, Unavailable):
        print(" ", got)
    else:
        for k, v in got.items():
            print(f"  {k:8s} {v.value:>12,.2f}   {v.cite()[:90]}")
    print("── 환율 ──"); print(" ", exchange_rate())
    print("── 주문 경로 차단 확인 ──")
    for p in ("/api/v1/orders", "/api/v1/holdings", "/api/v1/accounts"):
        try:
            _get(p)
            print(f"  {p}: 통과됨 ← 문제")
        except OrderPathBlocked:
            print(f"  {p}: 차단 OK")
