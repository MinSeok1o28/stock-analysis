"""출처 규칙의 구현체.

CLAUDE.md의 '출처 없는 숫자는 쓰지 않는다'를 프롬프트 지시가 아니라 실행되는 제약으로 만든다.
조사에서 확인한 실패 사례: 출처를 스키마로 선언만 하고 검증하지 않으면 전부 not_verified로 남는다.
그래서 여기서는 선언이 아니라 생성 자체를 막는다.

강제하는 규칙 3가지
  1. 3차 출처(웹검색·스크래핑)로는 수치 사실을 만들 수 없다.
  2. 페이지 번호는 로컬 문서에만 붙일 수 있다. 웹/API 출처는 URL만.
  3. 렌더 계층은 맨 숫자를 받지 않는다. require_sourced()가 거부한다.

의존: models.py 만. 네트워크를 모른다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import IntEnum, Enum
from pathlib import Path
from typing import Any, Generic, TypeVar

T = TypeVar("T")

LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", "ledger/manifest.jsonl"))


class Tier(IntEnum):
    """출처 등급. 낮을수록 권위가 높다."""

    PRIMARY = 1   # 발행 주체 직접 배포 — SEC, OpenDART, FRED, ECOS, ECB, ETF 발행사
    VENDOR = 2    # 벤더 정규화·집계 — FMP, Alpha Vantage
    SCRAPED = 3   # 비공식 스크래핑·웹검색 — yfinance, 검색결과

    @property
    def allows_numeric(self) -> bool:
        return self is not Tier.SCRAPED

    @property
    def label(self) -> str:
        return {Tier.PRIMARY: "1차", Tier.VENDOR: "2차", Tier.SCRAPED: "3차"}[self]


class SourceKind(Enum):
    LOCAL_DOCUMENT = "local_document"   # 업로드·다운로드한 원문 파일 → 페이지 인용 허용
    API = "api"                         # 구조화 응답 → URL만
    WEB = "web"                         # 웹페이지·검색결과 → URL만, 수치 금지
    USER_INPUT = "user_input"           # 사람이 직접 적은 값 (portfolio/*.yaml) → path 필수


class ProvenanceError(Exception):
    """출처 규칙 위반. 조용히 넘기지 않고 실패시킨다."""


@dataclass(frozen=True)
class Locator:
    """이 값이 원문의 어디에 있는가.

    page는 LOCAL_DOCUMENT 에만 허용된다. 웹 출처에 페이지 번호를 붙이는 것은
    가장 위험한 환각 유형이므로 생성 단계에서 막는다.
    """

    url: str | None = None
    path: str | None = None
    page: int | None = None
    section: str | None = None

    def cite(self) -> str:
        bits = []
        if self.path:
            bits.append(self.path)
        if self.page is not None:
            bits.append(f"p.{self.page}")
        if self.section:
            bits.append(self.section)
        if self.url:
            bits.append(self.url)
        return " · ".join(bits) if bits else "위치 미상"


@dataclass(frozen=True)
class Source:
    name: str
    tier: Tier
    kind: SourceKind
    locator: Locator
    retrieved_at: str = ""

    def __post_init__(self) -> None:
        if self.locator.page is not None and self.kind is not SourceKind.LOCAL_DOCUMENT:
            raise ProvenanceError(
                f"{self.name}: {self.kind.value} 출처에 페이지 번호를 붙일 수 없다. "
                "웹·API 출처는 URL만 인용한다 (CLAUDE.md 출처 규칙)."
            )
        if self.kind in (SourceKind.API, SourceKind.WEB) and not self.locator.url:
            raise ProvenanceError(f"{self.name}: 웹·API 출처는 URL이 필수다.")
        if self.kind in (SourceKind.LOCAL_DOCUMENT, SourceKind.USER_INPUT) and not self.locator.path:
            raise ProvenanceError(f"{self.name}: {self.kind.value} 출처는 path가 필수다.")
        if not self.retrieved_at:
            object.__setattr__(
                self, "retrieved_at", datetime.now(timezone.utc).isoformat(timespec="seconds")
            )

    def cite(self) -> str:
        return f"[{self.tier.label}] {self.name} — {self.locator.cite()}"


@dataclass(frozen=True)
class Sourced(Generic[T]):
    """값 + 출처. 시스템 안쪽에서는 모든 외부 유래 값이 이 형태로만 다닌다."""

    value: T
    source: Source

    def __post_init__(self) -> None:
        if isinstance(self.value, (int, float)) and not isinstance(self.value, bool):
            if not self.source.tier.allows_numeric:
                raise ProvenanceError(
                    f"{self.source.name}: 3차 출처로 수치 사실을 만들 수 없다 "
                    f"(값={self.value!r}). 웹검색은 정성 분석 전용이다."
                )

    def map(self, fn) -> "Sourced":
        """출처를 유지한 채 값만 변환한다."""
        return replace(self, value=fn(self.value))

    def cite(self) -> str:
        return self.source.cite()

    def __str__(self) -> str:
        return f"{self.value}  ⟨{self.cite()}⟩"


UNKNOWN = "확인 필요"


@dataclass(frozen=True)
class Unavailable:
    """데이터를 얻지 못했다는 사실 자체를 값으로 표현한다.

    조용히 웹검색으로 대체하거나 None으로 흘려보내지 않기 위해 존재한다.
    렌더 단계에서 '확인 필요 (이유)'로 표기된다.
    """

    what: str
    reason: str

    def cite(self) -> str:
        return f"{UNKNOWN} ({self.reason})"

    def __str__(self) -> str:
        return f"{UNKNOWN} — {self.what}: {self.reason}"


def require_sourced(name: str, obj: Any) -> Sourced | Unavailable:
    """렌더 계층의 문지기. 맨 숫자·문자열을 거부한다."""
    if isinstance(obj, (Sourced, Unavailable)):
        return obj
    raise ProvenanceError(
        f"'{name}'이 출처 없이 렌더로 전달됐다 (type={type(obj).__name__}). "
        "Sourced로 감싸거나, 얻지 못했다면 Unavailable로 명시하라."
    )


def record(sourced: Sourced | Unavailable, *, subject: str = "") -> None:
    """ledger/manifest.jsonl 에 한 줄 추가한다 (append-only).

    어제 산출물의 숫자를 오늘 재검증할 수 있게 만드는 유일한 장치다.
    """
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(sourced, Unavailable):
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "subject": subject,
            "status": "unavailable",
            "what": sourced.what,
            "reason": sourced.reason,
        }
    else:
        s = sourced.source
        row = {
            "ts": s.retrieved_at,
            "subject": subject,
            "status": "ok",
            "source": s.name,
            "tier": int(s.tier),
            "kind": s.kind.value,
            "locator": {k: v for k, v in vars(s.locator).items() if v is not None},
        }
    with LEDGER_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── 출처 팩토리: sources/ 모듈이 자기 등급을 여기서 선언한다 ──────────────

def primary_api(name: str, url: str, **loc) -> Source:
    return Source(name, Tier.PRIMARY, SourceKind.API, Locator(url=url, **loc))


def vendor_api(name: str, url: str, **loc) -> Source:
    return Source(name, Tier.VENDOR, SourceKind.API, Locator(url=url, **loc))


def local_filing(name: str, path: str, page: int | None = None, section: str | None = None) -> Source:
    return Source(
        name, Tier.PRIMARY, SourceKind.LOCAL_DOCUMENT,
        Locator(path=path, page=page, section=section),
    )


def user_input(name: str, path: str, section: str | None = None) -> Source:
    """사람이 직접 적은 값. 보유 현황·수동 실적일 등.

    등급은 1차다 — 자기 계좌의 보유 수량에 대해 사람이 최종 권위를 갖는다.
    다만 `retrieved_at`(=파일을 읽은 시각)과 파일의 `updated` 날짜를 함께 남겨
    값이 언제 기준인지 항상 드러나게 한다.
    """
    return Source(name, Tier.PRIMARY, SourceKind.USER_INPUT,
                  Locator(path=path, section=section))


def web(name: str, url: str) -> Source:
    """정성 전용. 이 출처로 수치를 만들면 Sourced가 거부한다."""
    return Source(name, Tier.SCRAPED, SourceKind.WEB, Locator(url=url))
