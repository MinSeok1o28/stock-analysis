"""도메인 어휘.

이 파일이 존재하는 이유: 자산 유형별로 다른 잣대를 쓰는 로직이 콕핏·계산·렌더
세 곳에 흩어져 있었다. 분류를 여기 한 곳에 두면 새 자산 유형 추가가 1개 파일 수정이 된다.

의존: 없음. 가장 안쪽 계층이므로 아무것도 import하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class ValuationBasis(Enum):
    """이 자산을 무슨 잣대로 평가할 수 있는가."""

    FUNDAMENTAL = "fundamental"       # 재무제표 + 역DCF 가능
    INDEX_AGGREGATE = "index_total"   # 개별 펀더멘털 대신 지수 총계로
    NO_FUNDAMENTAL = "none"           # 펀더멘털이 애초에 없음 → 대체 지표 사용
    PAR_AND_YIELD = "par_yield"       # 액면·이자율 기반


class AssetType(Enum):
    """자산 유형. 새 유형 추가는 여기 한 줄 + BASIS/ALT_METRICS 한 줄이면 끝난다."""

    SINGLE_STOCK = "single_stock"
    REIT = "reit"
    INDEX_ETF = "index_etf"
    SECTOR_ETF = "sector_etf"
    COMMODITY_ETF = "commodity_etf"
    BOND_ETF = "bond_etf"
    CASH = "cash"

    @property
    def basis(self) -> ValuationBasis:
        return _BASIS[self]

    @property
    def alt_metrics(self) -> tuple[str, ...]:
        """펀더멘털이 없거나 부족할 때 대신 봐야 하는 것."""
        return _ALT_METRICS.get(self, ())

    @property
    def supports_reverse_dcf(self) -> bool:
        return self.basis is ValuationBasis.FUNDAMENTAL

    @property
    def has_equity_constituents(self) -> bool:
        """룩스루 대상인가. 금·채권 ETF는 주식 구성종목이 없으므로 조회 자체가 무의미하다."""
        return self in (AssetType.INDEX_ETF, AssetType.SECTOR_ETF)


_BASIS: dict[AssetType, ValuationBasis] = {
    AssetType.SINGLE_STOCK: ValuationBasis.FUNDAMENTAL,
    AssetType.REIT: ValuationBasis.FUNDAMENTAL,
    AssetType.INDEX_ETF: ValuationBasis.INDEX_AGGREGATE,
    AssetType.SECTOR_ETF: ValuationBasis.INDEX_AGGREGATE,
    AssetType.COMMODITY_ETF: ValuationBasis.NO_FUNDAMENTAL,
    AssetType.BOND_ETF: ValuationBasis.PAR_AND_YIELD,
    AssetType.CASH: ValuationBasis.NO_FUNDAMENTAL,
}

_ALT_METRICS: dict[AssetType, tuple[str, ...]] = {
    AssetType.REIT: ("FFO", "AFFO", "점유율", "실질금리"),
    AssetType.COMMODITY_ETF: ("실질금리", "달러 방향", "재고·수급"),
    AssetType.BOND_ETF: ("듀레이션", "만기수익률", "신용스프레드"),
    AssetType.INDEX_ETF: ("지수 총계 PER", "지수 총계 이익성장률"),
    AssetType.SECTOR_ETF: ("지수 총계 PER", "섹터 이익 모멘텀"),
    AssetType.CASH: ("기준금리", "실질수익률"),
}


class Market(Enum):
    US = "US"
    KR = "KR"

    @classmethod
    def of_ticker(cls, ticker: str) -> "Market":
        """티커 표기로 시장을 가른다.

        KRX 는 6자리 종목코드를 쓴다. 신형 코드(0155E0, 0220W0)는 문자가 섞이지만
        **첫 글자는 항상 숫자**다. 미국 티커는 알파벳으로 시작한다.
        """
        t = (ticker or "").strip().upper()
        return cls.KR if len(t) == 6 and t[:1].isdigit() else cls.US

    @property
    def label(self) -> str:
        return "한국" if self is Market.KR else "미국"

    @property
    def transcript_availability(self) -> str:
        """스토리 리더가 스스로 한계를 밝히기 위한 정보."""
        return {
            Market.US: "어닝콜 전문 공개가 일반적",
            Market.KR: "어닝콜 전문 공개가 드묾 — 스토리 분석 정밀도 낮음",
        }[self]


@dataclass(frozen=True)
class Holding:
    """보유 1건. portfolio/holdings.yaml 한 항목에 대응."""

    ticker: str
    name: str
    asset_type: AssetType
    market: Market
    quantity: float
    avg_cost: float
    currency: str

    @property
    def book_value(self) -> float:
        return self.quantity * self.avg_cost

    @property
    def is_foreign_currency(self) -> bool:
        return self.currency != "KRW"


@dataclass(frozen=True)
class Filing:
    """공시 1건."""

    ticker: str
    form_type: str          # "10-K", "10-Q", "사업보고서"
    fiscal_year: int
    filed_on: date
    accession: str | None = None
    primary_document: str | None = None   # Archives 에서 본문을 찾는 파일명
    cik: str | None = None
    local_path: str | None = None   # 있으면 페이지 인용이 허용된다


@dataclass(frozen=True)
class CorporateAction:
    """가격 비교 가능성을 깨는 기업행위 공시 1건.

    액면분할·병합·감자·거래정지·상장폐지가 여기 들어온다.
    급등락 랭킹의 등락률이 왜 그 값인지 **확정**하는 근거다 (core/anomalies.py 는 정황까지만 만든다).
    """

    ticker: str
    filed_on: date
    title: str              # 공시 제목 원문 (report_nm)
    accession: str | None = None

    @property
    def url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.accession or ''}"

    def __str__(self) -> str:
        return f"{self.filed_on.isoformat()} {self.title}"


@dataclass(frozen=True)
class FinancialFact:
    """재무 사실 1건. 값은 반드시 Sourced로 감싸서 다닌다 (provenance.py 참조)."""

    concept: str            # "Revenues", "FreeCashFlow"
    unit: str               # "USD", "KRW", "shares"
    period_end: date
    fiscal_year: int
    value: float


class SignalKind(Enum):
    """일일 브리핑이 낼 수 있는 신호. '무엇을 더 파볼지'만 담는다.

    매수/매도 신호는 이 열거에 존재하지 않는다 — 구조적으로 낼 수 없다.
    """

    RUN_STORY_READER = "스토리 리더 권장"
    RUN_PRICE_DECODER = "가격 판독기 권장"
    RUN_COMPANY_DECODER = "기업 해독기 권장"
    CHECK_FX_EXPOSURE = "환노출 재점검 권장"
    DATA_GAP = "데이터 미확보 — 확인 필요"


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    ticker: str | None
    reason: str
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        banned = ("매수", "매도", "사라", "팔아", "buy", "sell", "목표주가")
        low = self.reason.lower()
        for w in banned:
            if w.lower() in low:
                raise ValueError(
                    f"신호 문구에 매매 표현이 포함됨: {w!r}. "
                    "CLAUDE.md의 '매매 신호 금지'는 구조적으로 강제된다."
                )
