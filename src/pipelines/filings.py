"""최신 정기보고서 참조. 미국(SEC 10-K)·한국(DART 사업보고서)를 한 어휘로 묶는다.

## 왜 필요한가
서사(`portfolio/narratives/<티커>.yaml`)에는 `updated` 날짜만 있었다. 그래서
**어느 보고서를 근거로 쓴 해석인지** 알 수 없었고, 새 10-K 가 나와도 그 서사가
낡았는지 판단할 방법이 없었다. 날짜만으로는 안 된다 — 90일이 지나도 새 보고서가
없으면 안 낡은 것이고, 30일밖에 안 지났어도 새 10-K 가 나왔으면 낡은 것이다.

**시간이 아니라 사건으로 판단해야 한다.** 그 사건의 식별자가 접수번호다:
미국은 accession, 한국은 rcept_no. 둘 다 **제출되면 다시 바뀌지 않는다.**

## 경계
여기서 네트워크를 타는 건 `latest()` 하나뿐이다. `check_basis()` 는 인자 둘을 비교하는
순수 함수라 네트워크 없이 테스트된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ..models import Market
from ..narrative_io import FilingBasis, Narrative
from ..provenance import Unavailable
from ..sources import open_dart, sec_edgar


@dataclass(frozen=True)
class FilingRef:
    """정기보고서 1건의 시장 무관 참조."""

    ticker: str
    market: Market
    form: str                # "10-K" · "사업보고서"
    filed_on: date
    accession: str
    url: str = ""

    @property
    def label(self) -> str:
        """회계연도 대신 접수일로 적는다.

        SEC 경로의 `Filing.fiscal_year` 는 제출일의 연도라 실제 회계연도와 한 해 어긋난다
        (FY2025 를 담은 10-K 가 2026-02 에 제출되면 2026 으로 잡힌다).
        확실한 사실만 적는 편이 낫다.
        """
        return f"{self.form} ({self.filed_on.isoformat()} 접수)"

    def to_basis(self) -> FilingBasis:
        return FilingBasis(form=self.form, filed_on=self.filed_on,
                           accession=self.accession, url=self.url)


def latest(ticker: str) -> FilingRef | Unavailable:
    """최신 정기보고서 1건. 시장은 티커 표기로 가른다.

    가벼운 조회다 — SEC 는 `submissions`, DART 는 `list.json` 이고 둘 다
    재무 본문(companyfacts·10-K)보다 훨씬 작다. 그래서 자주 불러도 된다.
    """
    tk = ticker.upper()
    market = Market.of_ticker(tk)

    if market is Market.KR:
        fs = open_dart.annual_filings(tk, limit=1)
        if isinstance(fs, Unavailable):
            return fs
        f = fs[0]
        return FilingRef(tk, market, f.form_type, f.filed_on, f.accession or "",
                         open_dart.filing_viewer_url(f))

    fs = sec_edgar.annual_filings(tk, limit=1)
    if isinstance(fs, Unavailable):
        return fs
    f = fs[0]
    return FilingRef(tk, market, f.form_type, f.filed_on, f.accession or "",
                     sec_edgar.filing_url(f) or "")


class BasisState(Enum):
    """서사가 근거로 삼은 보고서가 아직 최신인가."""

    CURRENT = "최신 보고서 기준"
    STALE = "새 보고서 나옴"
    UNRECORDED = "근거 보고서 미기록"
    UNKNOWN = "확인 불가"


@dataclass(frozen=True)
class BasisCheck:
    state: BasisState
    detail: str

    @property
    def is_warning(self) -> bool:
        """화면에 배지를 띄울 값어치가 있는가. '최신' 은 조용히 넘어간다."""
        return self.state in (BasisState.STALE, BasisState.UNRECORDED)

    def __str__(self) -> str:
        return f"[{self.state.value}] {self.detail}" if self.detail else f"[{self.state.value}]"


def check_basis(narrative: Narrative, current: FilingRef | Unavailable | None) -> BasisCheck:
    """서사의 근거 보고서를 현재 최신과 대조한다. 순수 함수 — 네트워크를 타지 않는다."""
    if narrative.is_empty:
        return BasisCheck(BasisState.UNKNOWN, "서사가 없다")
    if narrative.basis.is_empty:
        return BasisCheck(
            BasisState.UNRECORDED,
            "근거 보고서를 기록하기 전에 만들어진 서사다 — 재생성하면 기록된다")
    if current is None or isinstance(current, Unavailable):
        why = current.reason[:60] if isinstance(current, Unavailable) else "최신 보고서 미조회"
        return BasisCheck(BasisState.UNKNOWN,
                          f"{narrative.basis.label} 기준 · 최신 대조 불가 — {why}")
    if narrative.basis.accession == current.accession:
        return BasisCheck(BasisState.CURRENT, f"{current.label} 기준")
    return BasisCheck(
        BasisState.STALE,
        f"서사는 {narrative.basis.label} 기준 · 그 뒤 {current.label} 이 나왔다")


if __name__ == "__main__":
    import sys

    from ..narrative_io import load

    for tk in (sys.argv[1:] or ["NVDA", "005930"]):
        tk = tk.upper()
        cur = latest(tk)
        print(f"── {tk} ──")
        print(f"  최신 보고서: {cur if isinstance(cur, Unavailable) else cur.label}")
        if not isinstance(cur, Unavailable):
            print(f"    {cur.accession}  {cur.url}")
        print(f"  서사 대조:   {check_basis(load(tk), cur)}")
