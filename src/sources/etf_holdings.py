"""1차 출처: ETF 발행사 구성종목. 키 불필요.

콕핏의 '숨은 중복 노출'은 이 데이터 없이는 추정일 뿐이다.
개별주 AAPL을 들고 있는데 SPY도 들고 있으면 실제 AAPL 노출은 둘의 합이다.

## 신선도 — 반드시 산출물에 표기한다
- 발행사 일간 파일: 보통 **영업일 1일 지연(T+1)**. 파일 안의 'As of' 날짜를 그대로 전달한다.
- SEC N-PORT: 분기말 기준 **약 60일 후** 공개 → 1~4개월 묵은 값. (현재 미구현)

## 지원 현황 (2026-08-24 실측)
- **SSGA / SPDR** (SPY, DIA, XLK, XLF …): xlsx 직접 배포. **동작**.
  xlsx는 zip+XML이라 stdlib로 파싱한다 (openpyxl 의존성 없음).
- **iShares / Vanguard / Invesco**: 봇 차단(HTML 반환 또는 406). 자동 수집 불가.
  → `portfolio/etf_holdings/<TICKER>.csv` 에 직접 내려받아 두면 그걸 읽는다.
     (`portfolio/` 아래인 이유: 사람이 넣은 파일이므로 `data/` 를 비워도 살아남아야 한다.)
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from ..models import AssetType, Holding
from ..provenance import Sourced, Unavailable, local_filing, primary_api
from ._http import CACHE_DIR, SourceUnavailable, throttle

SSGA_URL = "https://www.ssga.com/us/en/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{t}.xlsx"
MANUAL_DIR = Path("portfolio/etf_holdings")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

#: 자동 수집이 막힌 발행사. 안내 문구를 정확히 주기 위해 티커 접두로 추정한다.
BLOCKED_HINT = ("iShares·Vanguard·Invesco 는 자동 수집이 차단돼 있다. "
                "발행사 페이지에서 보유종목 CSV를 내려받아 "
                "portfolio/etf_holdings/{t}.csv 로 저장하라")


def _xlsx_rows(blob: bytes) -> list[list[str]]:
    """xlsx = zip + XML. stdlib만으로 시트1을 행 목록으로 만든다."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in root]
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(f"{_NS}row"):
        vals = []
        for c in row.iter(f"{_NS}c"):
            v = c.find(f"{_NS}v")
            if v is None or v.text is None:
                vals.append("")
            elif c.get("t") == "s":
                vals.append(shared[int(v.text)])
            else:
                vals.append(v.text)
        rows.append(vals)
    return rows


def _as_of(rows: list[list[str]]) -> str:
    for r in rows[:6]:
        for cell in r:
            m = re.search(r"As of\s+(.+)", str(cell), re.I)
            if m:
                return m.group(1).strip()
    return "기준일 미상"


def _weights(rows: list[list[str]], ticker_col: str = "Ticker",
             weight_col: str = "Weight") -> dict[str, float]:
    hdr_i = next((i for i, r in enumerate(rows)
                  if ticker_col in r and weight_col in r), None)
    if hdr_i is None:
        return {}
    hdr = rows[hdr_i]
    ti, wi = hdr.index(ticker_col), hdr.index(weight_col)
    out: dict[str, float] = {}
    for r in rows[hdr_i + 1:]:
        if len(r) <= max(ti, wi):
            continue
        t = str(r[ti]).strip().upper()
        if not t or t in ("-", "--", "CASH", "USD"):
            continue        # 현금·미분류 행은 종목 노출이 아니다
        try:
            w = float(str(r[wi]).replace(",", "")) / 100.0
        except (TypeError, ValueError):
            continue
        if w > 0:
            out[t] = out.get(t, 0.0) + w
    return out


def _fetch(url: str, cache_name: str, ttl_sec: int = 43_200) -> bytes:
    """발행사 파일은 바이너리라 _http.get_json 을 쓸 수 없다. 캐시는 동일 정책."""
    import time
    cached = CACHE_DIR / "etf" / cache_name
    if cached.exists() and (time.time() - cached.stat().st_mtime) < ttl_sec:
        return cached.read_bytes()
    throttle(url.split("/")[2], 1.0)
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=40)
        r.raise_for_status()
    except requests.RequestException as exc:
        if cached.exists():
            return cached.read_bytes()
        raise SourceUnavailable(f"{exc}") from exc
    if r.content[:2] != b"PK":          # xlsx 는 zip 시그니처로 시작한다
        raise SourceUnavailable("xlsx 가 아닌 응답 (차단 또는 티커 미지원)")
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(r.content)
    return r.content


def _from_manual(ticker: str) -> Sourced[dict[str, float]] | None:
    """사람이 내려받아 둔 CSV. 발행사가 자동 수집을 막은 경우의 경로."""
    path = MANUAL_DIR / f"{ticker.upper()}.csv"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.search(r"\bticker\b", ln, re.I) and re.search(r"\bweight\b", ln, re.I)), 0)
    rows = list(csv.reader(lines[start:]))
    if not rows:
        return None
    hdr = [h.strip() for h in rows[0]]
    tcol = next((h for h in hdr if re.fullmatch(r"ticker|symbol", h, re.I)), None)
    wcol = next((h for h in hdr if re.search(r"weight", h, re.I)), None)
    if not tcol or not wcol:
        return None
    w = _weights([hdr] + rows[1:], tcol, wcol)
    if not w:
        return None
    mtime = date.fromtimestamp(path.stat().st_mtime).isoformat()
    return Sourced(w, local_filing(f"{ticker.upper()} 구성종목 (수동 공급)",
                                   str(path), section=f"파일 수정일 {mtime}"))


def holdings(etf_ticker: str) -> Sourced[dict[str, float]] | Unavailable:
    """{구성종목: 비중(소수)}. 비중 합은 보통 1.0 근처지만 정확히 1은 아니다."""
    t = etf_ticker.upper()
    manual = _from_manual(t)
    if manual is not None:
        return manual
    url = SSGA_URL.format(t=t.lower())
    try:
        rows = _xlsx_rows(_fetch(url, f"ssga_{t}.xlsx"))
    except (SourceUnavailable, zipfile.BadZipFile, ET.ParseError) as exc:
        return Unavailable(f"{t} 구성종목",
                           f"발행사 자동 수집 실패({exc}). " + BLOCKED_HINT.format(t=t))
    w = _weights(rows)
    if not w:
        return Unavailable(f"{t} 구성종목", f"파일 형식 인식 실패 — {url}")
    return Sourced(w, primary_api(f"SPDR {t} 구성종목 (발행사 일간)", url,
                                  section=f"{_as_of(rows)} · 통상 T+1 지연"))


def equity_etfs(holdings: list[Holding]) -> list[str]:
    """룩스루 대상 ETF만. 금·채권 ETF는 주식 구성종목이 없어 제외한다."""
    return [h.ticker for h in holdings if h.asset_type.has_equity_constituents]


def look_through_map(etf_tickers: list[str]) -> tuple[dict[str, dict[str, float]], list[Unavailable]]:
    """concentration.look_through() 입력 + 실패 목록.

    실패를 조용히 버리지 않는다 — 콕핏이 '집중도 과소평가' 경고를 띄우는 근거가 된다.
    """
    ok: dict[str, dict[str, float]] = {}
    missing: list[Unavailable] = []
    for t in etf_tickers:
        r = holdings(t)
        if isinstance(r, Unavailable):
            missing.append(r)
        else:
            ok[t.upper()] = r.value
    return ok, missing


if __name__ == "__main__":
    import sys
    for t in (sys.argv[1:] or ["SPY"]):
        r = holdings(t)
        if isinstance(r, Unavailable):
            print(f"{t}: {r}")
            continue
        w = r.value
        print(f"── {t}: {len(w)}종목 · 비중합 {sum(w.values()):.1%}")
        print(f"   {r.cite()[:110]}")
        for k, v in sorted(w.items(), key=lambda kv: -kv[1])[:8]:
            print(f"     {k:6s} {v:6.2%}")
