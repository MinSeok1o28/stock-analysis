"""보유·관심 종목 로더. 사람이 쓴 YAML ↔ 도메인 객체.

## 왜 sources/ 가 아닌가
`sources/`는 **벤더가 바꿔서** 바뀌는 코드다. 이 파일은 **우리 파일 형식이 바뀔 때만** 바뀐다.
변경 이유가 다르므로 다른 곳에 둔다. (I/O이긴 하지만 계층 기준은 I/O 유무가 아니라 변경 이유다.)

## 왜 값을 Sourced 로 감싸나
보유 수량·평단은 산출물에 숫자로 나가는 값이다. 출처 없는 숫자를 렌더로 넘길 수 없으므로
`SourceKind.USER_INPUT`(1차 — 자기 계좌에 대해 사람이 최종 권위)으로 감싼다.
파일의 `updated` 날짜가 함께 붙어 값이 언제 기준인지 항상 드러난다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from .models import AssetType, Holding, Market
from .provenance import Sourced, Unavailable, user_input

HOLDINGS_PATH = Path("portfolio/holdings.yaml")
WATCHLIST_PATH = Path("portfolio/watchlist.yaml")
SNAPSHOT_DIR = Path("portfolio/snapshots")

STALE_AFTER_DAYS = 7
PLACEHOLDER_TICKERS = {"EXAMPLE"}


class PortfolioError(Exception):
    """파일 형식 오류. 조용히 넘기지 않는다 — 잘못된 보유 현황은 모든 계산을 오염시킨다."""


@dataclass(frozen=True)
class Portfolio:
    holdings: list[Sourced[Holding]]
    updated: date | None
    base_currency: str
    path: Path

    @property
    def tickers(self) -> list[str]:
        """시세 조회용 심볼 목록. 토스는 이걸 통째로 1콜에 넘긴다."""
        return [s.value.ticker for s in self.holdings]

    @property
    def is_empty(self) -> bool:
        return not self.holdings

    def by_type(self) -> dict[AssetType, list[Sourced[Holding]]]:
        out: dict[AssetType, list[Sourced[Holding]]] = {}
        for s in self.holdings:
            out.setdefault(s.value.asset_type, []).append(s)
        return out

    def staleness(self, today: date | None = None) -> tuple[int, bool]:
        """(경과일, 갱신 권고 여부). 콕핏이 오래된 보유 현황으로 계산하지 않게 한다."""
        if self.updated is None:
            return (-1, True)
        days = ((today or date.today()) - self.updated).days
        return (days, days > STALE_AFTER_DAYS)


def _as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None
    return None


def _enum(cls, raw, field: str, ctx: str):
    if raw is None:
        raise PortfolioError(f"{ctx}: '{field}' 누락")
    try:
        return cls(str(raw).strip())
    except ValueError:
        allowed = ", ".join(m.value for m in cls)
        raise PortfolioError(
            f"{ctx}: '{field}' 값 '{raw}' 를 인식할 수 없다. 허용: {allowed}"
        ) from None


def load_holdings(path: Path = HOLDINGS_PATH) -> Portfolio | Unavailable:
    if not path.exists():
        return Unavailable("보유 현황", f"{path} 없음 — 파일을 만들고 보유 종목을 적으라")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return Unavailable("보유 현황", f"{path} YAML 오류: {exc}")

    updated = _as_date(doc.get("updated"))
    base_ccy = str(doc.get("base_currency", "KRW"))
    rows = doc.get("holdings") or []
    if not isinstance(rows, list):
        return Unavailable("보유 현황", f"{path}: 'holdings' 는 목록이어야 한다")

    src_note = f"updated={updated.isoformat() if updated else '미기재'}"
    out: list[Sourced[Holding]] = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise PortfolioError(f"{path} holdings[{i}]: 매핑이 아니다")
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            raise PortfolioError(f"{path} holdings[{i}]: 'ticker' 누락")
        if ticker in PLACEHOLDER_TICKERS:
            continue                      # 템플릿 예시 항목은 건너뛴다
        ctx = f"{path} holdings[{i}] ({ticker})"
        try:
            qty = float(row.get("quantity", 0))
            cost = float(row.get("avg_cost", 0))
        except (TypeError, ValueError) as exc:
            raise PortfolioError(f"{ctx}: quantity/avg_cost 가 숫자가 아니다 — {exc}") from None
        if qty < 0 or cost < 0:
            raise PortfolioError(f"{ctx}: quantity/avg_cost 는 음수일 수 없다")

        h = Holding(
            ticker=ticker,
            name=str(row.get("name", ticker)),
            asset_type=_enum(AssetType, row.get("asset_type"), "asset_type", ctx),
            market=_enum(Market, row.get("market"), "market", ctx),
            quantity=qty,
            avg_cost=cost,
            currency=str(row.get("currency", base_ccy)).upper(),
        )
        out.append(Sourced(h, user_input(f"보유 현황 {ticker}", str(path), src_note)))

    dupes = {t for t in (s.value.ticker for s in out)
             if [x.value.ticker for x in out].count(t) > 1}
    if dupes:
        raise PortfolioError(f"{path}: 중복 티커 {sorted(dupes)} — 한 줄로 합치라")

    return Portfolio(out, updated, base_ccy, path)


@dataclass(frozen=True)
class WatchItem:
    ticker: str
    note: str = ""
    earnings_date: date | None = None
    earnings_confirmed: bool = False   # 회사 공식 발표면 True, 과거 패턴 추정이면 False
    earnings_source: str = ""          # 어디서 확인했는가 (수동 입력이라 스스로 밝힌다)

    def days_to_earnings(self, today: date | None = None) -> int | None:
        if self.earnings_date is None:
            return None
        return (self.earnings_date - (today or date.today())).days

    @property
    def certainty(self) -> str:
        return "확정" if self.earnings_confirmed else "추정"


def load_watchlist(path: Path = WATCHLIST_PATH) -> list[Sourced[WatchItem]] | Unavailable:
    """관심 종목 + 수동 실적일.

    실적 캘린더를 주는 무료 소스가 없으므로 실적일은 사람이 적는다.
    분기 1회 갱신이면 충분하고, 브리핑의 '실적 7일 이내' 신호가 이걸 읽는다.
    """
    if not path.exists():
        return Unavailable("관심 종목", f"{path} 없음")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return Unavailable("관심 종목", f"{path} YAML 오류: {exc}")

    updated = _as_date(doc.get("updated"))
    note = f"updated={updated.isoformat() if updated else '미기재'}"
    out: list[Sourced[WatchItem]] = []
    for row in doc.get("watching") or []:
        if isinstance(row, str):
            row = {"ticker": row}
        if not isinstance(row, dict):
            continue
        t = str(row.get("ticker", "")).strip().upper()
        if not t:
            continue
        out.append(Sourced(
            WatchItem(t, str(row.get("note", "")), _as_date(row.get("earnings_date")),
                      bool(row.get("earnings_confirmed", False)),
                      str(row.get("earnings_source", ""))),
            user_input(f"관심 종목 {t}", str(path), note),
        ))
    return out


def upcoming_earnings(items: list[Sourced[WatchItem]], within_days: int = 7,
                      today: date | None = None) -> list[Sourced[WatchItem]]:
    """브리핑의 RUN_STORY_READER 신호 입력. 지난 실적일은 제외한다."""
    out = []
    for s in items:
        d = s.value.days_to_earnings(today)
        if d is not None and 0 <= d <= within_days:
            out.append(s)
    return sorted(out, key=lambda s: s.value.earnings_date or date.max)


def write_snapshot(portfolio: Portfolio, on: date | None = None,
                   directory: Path = SNAPSHOT_DIR) -> Path:
    """수익 기여도 계산의 전제. holdings.yaml 을 바꿀 때마다 남긴다."""
    on = on or date.today()
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{on.isoformat()}.yaml"
    doc = {
        "updated": (portfolio.updated or on).isoformat(),
        "snapshot_taken": on.isoformat(),
        "base_currency": portfolio.base_currency,
        "holdings": [
            {"ticker": h.ticker, "name": h.name, "asset_type": h.asset_type.value,
             "market": h.market.value, "quantity": h.quantity,
             "avg_cost": h.avg_cost, "currency": h.currency}
            for h in (s.value for s in portfolio.holdings)
        ],
    }
    dest.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dest


def list_snapshots(directory: Path = SNAPSHOT_DIR) -> list[date]:
    out = []
    for f in directory.glob("*.yaml"):
        d = _as_date(f.stem)
        if d:
            out.append(d)
    return sorted(out)


def can_compute_contribution(directory: Path = SNAPSHOT_DIR) -> bool:
    """스냅샷이 2개 미만이면 수익 기여도를 산출할 수 없다 — 콕핏이 이걸 확인한다."""
    return len(list_snapshots(directory)) >= 2


if __name__ == "__main__":
    p = load_holdings()
    if isinstance(p, Unavailable):
        print(p)
    else:
        days, stale = p.staleness()
        print(f"보유 {len(p.holdings)}종목 · 기준통화 {p.base_currency} · "
              f"갱신 {p.updated} ({days}일 전{', 갱신 권고' if stale else ''})")
        for t, rows in p.by_type().items():
            print(f"  {t.value:14s} {len(rows):>2}종목  잣대={t.basis.value}"
                  + (f"  대체지표={','.join(t.alt_metrics)}" if t.alt_metrics else ""))
        print("  시세 조회 심볼:", p.tickers or "(없음)")
        print("  스냅샷:", list_snapshots(), "| 기여도 산출 가능:", can_compute_contribution())
