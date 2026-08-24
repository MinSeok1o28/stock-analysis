"""1차 출처: FRED (세인트루이스 연준). 무료 키 필요, 120 calls/min.

자주 쓰는 계열:
  DGS10   미 10년 국채 — 역DCF 할인율의 무위험수익률
  DGS2    미 2년 국채
  FEDFUNDS 연방기금금리
  CPIAUCSL 소비자물가지수
  DTWEXBGS 광범위 달러지수 — 원자재 ETF 평가 대체지표
"""

from __future__ import annotations

from ..provenance import Sourced, Unavailable, primary_api
from ._http import SourceUnavailable, get_json, require_env

BASE = "https://api.stlouisfed.org/fred"
NAME = "FRED"

COMMON = {
    "us10y": "DGS10", "us2y": "DGS2", "fedfunds": "FEDFUNDS",
    "cpi": "CPIAUCSL", "dollar_index": "DTWEXBGS",
}


def latest(series_id: str) -> Sourced[float] | Unavailable:
    sid = COMMON.get(series_id, series_id)
    try:
        key = require_env("FRED_API_KEY", "https://fred.stlouisfed.org/docs/api/api_key.html 에서 무료 발급")
        data = get_json(f"{BASE}/series/observations",
                        params={"series_id": sid, "api_key": key, "file_type": "json",
                                "sort_order": "desc", "limit": 5},
                        cache_key=f"{sid}_latest", ttl_sec=3_600, min_interval=0.5)
        obs = next(o for o in data["observations"] if o["value"] != ".")
    except (SourceUnavailable, StopIteration, KeyError) as exc:
        return Unavailable(f"FRED {sid}", str(exc))
    return Sourced(float(obs["value"]), primary_api(
        f"{NAME} {sid}", f"https://fred.stlouisfed.org/series/{sid}",
        section=f"관측일 {obs['date']}",
    ))


if __name__ == "__main__":
    for k in ("us10y", "fedfunds"):
        print(k, "→", latest(k))
