"""서사 자동 생성 — 사실 카드를 읽고 Claude 가 해석을 쓴다.

`claude` CLI 를 비대화형(`-p`)으로 호출한다. 구독 한도 안에서 돌고
별도 API 키를 쓰지 않는다 (CLAUDE.md 운영 원칙).

경계는 그대로다:
  파이프라인이 조립한 [사실] → 프롬프트로 전달
  Claude 가 [해석] 만 쓴다 → portfolio/narratives/<티커>.yaml
  **숫자는 전달된 사실에 있는 값만 쓰도록 프롬프트가 강제한다.**
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import yaml

from ..models import Market
from ..narrative_io import NARRATIVE_DIR, load, path_for
from ..provenance import Unavailable
from . import company_decoder as cd

TIMEOUT = 300
CLI = shutil.which("claude")


def available() -> bool:
    return CLI is not None


def facts_digest(ticker: str, on: date | None = None) -> str | Unavailable:
    """Claude 에게 넘길 사실 묶음. 카드에 있는 값만 담는다."""
    from ..core.valuation.fundamentals import earnings_quality
    from ..core.valuation.outliers import normalized_base
    from . import stock_page as sp

    pg = sp.build(ticker, on or date.today(), with_story=False)
    if isinstance(pg, Unavailable):
        return pg
    c, L = pg.card, []
    div, unit = (1e12, "조") if c.market is Market.KR else (1e9, "B")

    L.append(f"# {c.info.get('name') or ticker} ({ticker}) 사실 묶음")
    L.append(f"시장 {c.info.get('market','')} · 통화 {c.info.get('currency','')} · "
             f"자산유형 {c.asset_type.value}")
    if not isinstance(c.price, Unavailable):
        L.append(f"현재가 {c.price.value:,.2f}")
    if not isinstance(c.market_cap, Unavailable):
        L.append(f"시가총액 {c.market_cap.value/div:,.1f}{unit}")
    if c.net_debt is not None and not isinstance(c.net_debt, Unavailable):
        L.append(f"{c.net_debt.value}")

    for key, label in (("Revenues", "매출"), ("OperatingIncome", "영업이익"),
                       ("NetIncome", "순이익")):
        v = c.series.get(key)
        if v:
            L.append(f"{label}: " + " · ".join(
                f"FY{s.value.fiscal_year} {s.value.value/div:,.1f}{unit}" for s in v[-6:]))
    if not isinstance(c.fcf, Unavailable):
        vals = cd._vals(c.fcf)
        L.append("FCF: " + " · ".join(f"{k} {v/div:,.1f}{unit}" for k, v in vals[-6:]))
        L.append(f"FCF 3년 평균 {normalized_base(vals)/div:,.1f}{unit}")
    if pg.quality:
        L.append(f"이익-현금 정합성: {pg.quality}")
    if c.outliers:
        L.append("이상치: " + " / ".join(str(o) for o in c.outliers))

    if c.segments and getattr(c.segments, "tables", None):
        for k, t in c.segments.tables.items():
            L.append(f"[{k}] " + " · ".join(
                f"{g} 매출 {v/div:,.1f}{unit}({sh:.1%})"
                + (f" 영업이익률 {m:.1%}" if m is not None else "")
                for g, v, sh, m in t.shares()[:8]))

    if pg.implied is not None:
        L.append(f"역DCF: 요구 성장률 {pg.implied:.1%} (WACC {pg.wacc:.1%}, 영구성장 2.5%)")
    for b in pg.basis:
        if b.implied is not None:
            L.append(f"기준 FCF {b.label} {b.fcf/div:,.1f}{unit} → 요구 성장률 {b.implied:.2%}")
    for a in pg.axes:
        if a.value is not None:
            L.append(f"{a.label} {a.value:+.1%} ({a.note})")
    for g in pg.growth:
        L.append(f"가정 성장률 {g.growth:.1%} → 정당화 주가 {g.price:,.2f}"
                 + (f" ({g.vs_current:+.1%})" if g.vs_current is not None else "")
                 + (f" [{g.label}]" if g.label else ""))
    if pg.stat and pg.stat.n:
        L.append(f"과거 실적 반응: {pg.stat.summary()}")
        L.append("반응 상세: " + " · ".join(f"{e}→{r} {m:+.2%}"
                                          for e, r, m, _ in pg.stat.moves))
    if c.business_excerpt:
        L.append("\n[10-K Item 1 발췌]\n" + c.business_excerpt[:1800])
    if c.notes:
        L.append("\n[확인 필요]\n" + "\n".join(f"- {n}" for n in c.notes[:6]))
    return "\n".join(L)


PROMPT = """아래는 {ticker} 에 대해 파이프라인이 조립한 **검증된 사실**이다.
이 사실만 근거로 투자 리서치 서사를 쓴다.

## 절대 규칙
- **여기 없는 숫자를 만들지 마라.** 인용하는 모든 수치는 아래 사실 묶음에 있어야 한다.
- 매수·매도·목표주가를 말하지 마라. "어디를 더 봐야 하는지"만 쓴다.
- 확신할 수 없으면 "확인 필요"라고 쓴다.
- 문장 안에서 **중요한 부분은 `**강조**`** 로 감싼다 (굵은 글씨로 렌더된다).

## 출력 형식
순수 YAML 만 출력한다. 코드펜스·설명·인사말 금지. 아래 키를 모두 채운다.

ticker: {ticker}
updated: {today}
author: claude
one_liner: >-
  # 피터 린치 2분 룰 — 초등학생도 이해할 한 문장. 업종 용어 금지.
  # 이 회사가 뭘 파는지, 돈이 어디서 나오는지를 비중 숫자와 함께.
how_it_makes_money: >-
  # 세그먼트·제품·지역 표의 숫자를 근거로 3~4문단.
  # 매출 비중과 영업이익률이 벌어지는 지점을 반드시 짚는다.
story: >-
  # 재무 계열의 변곡점을 이야기로. 3~5문단.
  # 언제 무슨 일이 있었고, 지금 가격이 무엇을 요구하는지로 마무리.
risks:
  # **정확히 3개.** 영향이 큰 것만. detail 은 2~4문장.
  # evidence 에 근거 위치(세그먼트 표·FCF 계열·역DCF 등)를 적는다.
  - title: ""
    detail: ""
    evidence: ""
watch_next:
  # 3~4개. 다음에 확인할 것 + 어디서 확인하는지.
  - ""

## 사실 묶음
{facts}
"""


def _extract_yaml(text: str) -> str:
    t = text.strip()
    if "```" in t:
        parts = [p for p in t.split("```") if p.strip()]
        for p in parts:
            body = p[5:] if p.lower().startswith("yaml") else p
            if "ticker:" in body:
                return body.strip()
    i = t.find("ticker:")
    return t[i:].strip() if i >= 0 else t


def write(ticker: str, on: date | None = None, *, timeout: int = TIMEOUT
          ) -> Path | Unavailable:
    """사실을 모아 Claude 에게 넘기고, 돌아온 YAML 을 검증해 저장한다."""
    tk = ticker.upper()
    if not available():
        return Unavailable(f"{tk} 서사", "claude CLI 를 찾을 수 없다")
    facts = facts_digest(tk, on)
    if isinstance(facts, Unavailable):
        return facts
    prompt = PROMPT.format(ticker=tk, today=(on or date.today()).isoformat(), facts=facts)
    try:
        r = subprocess.run([CLI, "-p", prompt], capture_output=True, text=True,
                           timeout=timeout, cwd=str(Path.cwd()))
    except subprocess.TimeoutExpired:
        return Unavailable(f"{tk} 서사", f"claude CLI 응답 시간 초과 ({timeout}초)")
    if r.returncode != 0:
        return Unavailable(f"{tk} 서사", f"claude CLI 실패: {(r.stderr or '')[:160]}")

    raw = _extract_yaml(r.stdout)
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return Unavailable(f"{tk} 서사", f"YAML 파싱 실패: {exc}")
    if not isinstance(doc, dict) or not doc.get("one_liner"):
        return Unavailable(f"{tk} 서사", "응답에 one_liner 가 없다")

    doc["ticker"] = tk
    doc["updated"] = (on or date.today()).isoformat()
    doc["author"] = "claude"
    doc["risks"] = (doc.get("risks") or [])[:3]          # 3개로 강제

    # 어느 보고서를 근거로 쓴 해석인지 남긴다. 날짜만으로는 낡았는지 알 수 없다 —
    # 새 10-K 가 나왔는가로 판단해야 하고, 그 기준이 접수번호다 (pipelines/filings.py).
    from . import filings
    ref = filings.latest(tk)
    if not isinstance(ref, Unavailable):
        doc["based_on"] = {"form": ref.form, "filed_on": ref.filed_on.isoformat(),
                           "accession": ref.accession, "url": ref.url}
    NARRATIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = path_for(tk)
    dest.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                                   default_flow_style=False), encoding="utf-8")
    return dest


if __name__ == "__main__":
    import sys
    tk = (sys.argv[1] if len(sys.argv) > 1 else "TSLA").upper()
    print(f"claude CLI: {CLI or '없음'}")
    if "--facts" in sys.argv:
        print(facts_digest(tk)); sys.exit(0)
    print(f"{tk} 서사 생성 중… (최대 {TIMEOUT}초)")
    r = write(tk)
    if isinstance(r, Unavailable):
        print(r); sys.exit(1)
    n = load(tk)
    print(f"✓ {r}")
    print(f"  한 줄: {n.one_liner[:110]}")
    print(f"  리스크 {len(n.risks)}개 · 지켜볼 것 {len(n.watch_next)}개")
