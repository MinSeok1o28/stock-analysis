"""수치 해설 — 주식·회계를 모르는 사람이 표를 읽을 수 있게.

## 규칙
- **정의와 읽는 법만 쓴다.** "그래서 사라/팔아라"로 넘어가지 않는다 (CLAUDE.md 매매 신호 금지).
- 각 항목에 `caution` 을 둔다. 초보자가 가장 흔히 오해하는 지점을 미리 막는 자리다.
  예: 요구 성장률을 적정주가로 읽는 것, 주가만으로 회사 크기를 재는 것.
- 숫자를 만들지 않는다. 여기는 문구만 있고 값은 파이프라인이 준다.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class Term:
    key: str
    label: str
    one_line: str            # 이게 뭔가 — 한 문장
    how_to_read: str         # 어떻게 읽나
    caution: str = ""        # 가장 흔한 오해

    @property
    def tooltip(self) -> str:
        """표 헤더·셀의 title 속성용 평문."""
        bits = [self.one_line, f"읽는 법: {self.how_to_read}"]
        if self.caution:
            bits.append(f"주의: {self.caution}")
        return " / ".join(bits)


TERMS: tuple[Term, ...] = (
    Term(
        "market_cap", "시가총액",
        "회사 전체를 통째로 사려면 내야 하는 값. 주가 × 발행된 주식 수다.",
        "회사의 '크기'다. 삼성전자 25만원과 애플 310달러를 나란히 놓고 어느 쪽이 큰 "
        "회사인지 비교하려면 주가가 아니라 이 값을 봐야 한다.",
        "주가가 비싸다고 큰 회사가 아니다. 주식을 몇 조각으로 쪼갰느냐에 따라 "
        "주가는 얼마든지 달라진다."),
    Term(
        "implied", "요구 성장률",
        "지금 이 주가가 설명되려면 회사가 앞으로 몇 %씩 성장해야 하는지를 거꾸로 계산한 값.",
        "'시장이 이 회사에 걸고 있는 기대치'로 읽는다. 25%면 앞으로 매년 25%씩 커진다는 "
        "가정이 이미 가격에 들어가 있다는 뜻이다.",
        "**적정주가가 아니다.** 이 시스템은 '얼마가 맞다'를 말하지 않는다. "
        "가격에 담긴 가정을 꺼내 보여줄 뿐이다."),
    Term(
        "rev_cagr", "과거 매출 CAGR",
        "지난 몇 년간 매출이 실제로 연평균 몇 %씩 늘었는지. CAGR은 연평균 복리 성장률이다.",
        "요구 성장률과 짝지어 본다. 시장이 요구하는 속도와 이 회사가 실제로 내던 "
        "속도를 나란히 놓는 것이다.",
        "구간에 따라 부호까지 바뀐다 (애플은 3년 -3.9%인데 11년으로 늘리면 +7.3%). "
        "몇 년짜리인지 라벨을 꼭 확인한다."),
    Term(
        "gap", "격차",
        "요구 성장률 − 과거 매출 CAGR. 몇 %포인트 차이인지를 뺀 값이다.",
        "양수면 '지금 가격은 이 회사가 과거보다 빠르게 성장한다고 전제한다'는 뜻이고, "
        "음수면 '과거보다 느려져도 설명된다'는 뜻이다.",
        "**싸다·비싸다가 아니다.** 격차가 크면 '왜 그렇게 기대하는지'를 더 파보라는 "
        "신호일 뿐, 팔라는 뜻이 아니다. 실제로 더 빨리 자라는 회사도 많다."),
    Term(
        "fcf", "FCF (잉여현금흐름)",
        "장사해서 번 현금에서 공장·장비 같은 설비 투자를 빼고 남은 현금.",
        "회사가 실제로 손에 쥐는 돈이다. 빚을 갚거나 배당을 주거나 쌓아둘 수 있는 여윳돈이 "
        "이만큼이라는 뜻이다.",
        "'이익'과 다르다. 이익은 회계 규칙으로 계산한 숫자고 FCF는 실제 현금이다. "
        "둘이 오래 벌어지면 확인이 필요하다."),
    Term(
        "fcf_avg", "FCF 3년 평균",
        "최근 3년 FCF의 평균. 한 해만 보면 생기는 착시를 막는 값이다.",
        "최신 FCF 옆에 나란히 둔다. 최신값이 평균과 크게 다르면 그 해가 특이했다는 뜻이다.",
        "최신값이 3년 평균에서 ±40% 넘게 벗어나면 이 시스템은 평균을 계산 기준으로 "
        "바꾸고 그 사실을 적는다."),
    Term(
        "quality", "이익-현금 정합성",
        "장부상 이익과 실제 들어온 현금이 같은 방향으로 움직이는지 본 것.",
        "'같은 방향'이면 무난하다. '따로 논다'는 이익은 늘었는데 현금은 줄었다는 뜻으로, "
        "왜 그런지 확인이 필요하다.",
        "'따로 논다'가 곧 분식회계는 아니다. 일회성 손상이나 큰 투자 때문일 수도 있다. "
        "확인해보라는 표시일 뿐이다."),
    Term(
        "segment", "최대 부문",
        "매출이 가장 많이 나오는 사업 부문과 그 비중.",
        "한 부문 비중이 높을수록 그 사업 하나에 회사 전체가 달려 있다는 뜻이다. "
        "애플의 아이폰, 엔비디아의 데이터센터가 그런 경우다.",
        "쏠림이 나쁜 것만은 아니다. 잘하는 것 하나에 집중한 결과일 수도 있다. "
        "다만 그 하나가 흔들리면 회사가 흔들린다는 사실은 남는다."),
    Term(
        "reaction", "실적 반응",
        "과거 실적 발표 다음 거래일에 주가가 평균 몇 %(절댓값) 움직였는지.",
        "이 종목이 실적에 얼마나 예민한지를 본다. ±2%인 종목과 ±7%인 종목은 "
        "실적 발표를 앞두고 마음가짐이 달라야 한다.",
        "**예측이 아니라 과거 기록이다.** 다음에도 그만큼 움직인다는 보장은 없다."),
    Term(
        "wacc", "WACC (할인율)",
        "미래에 벌 돈을 오늘 가치로 바꿀 때 쓰는 이자율.",
        "내년의 100만원은 오늘의 100만원보다 가치가 낮다. 얼마나 낮게 볼지가 이 값이다. "
        "여기서는 미국 10년물 국채금리 + 위험프리미엄 4.5%로 잡는다.",
        "위험프리미엄 4.5%는 **가정**이다. 이 값을 바꾸면 요구 성장률도 바뀐다."),
)

BY_KEY = {t.key: t for t in TERMS}

INTRO = ("숫자를 처음 보시면 아래를 먼저 읽어보세요. "
         "이 표는 **어느 종목이 낫다고 말하지 않습니다.** "
         "각 회사가 어떤 상태이고 어디를 더 파봐야 하는지만 보여줍니다.")


def tooltip(key: str) -> str:
    """`title` 속성에 넣을 평문. 없는 키는 빈 문자열."""
    t = BY_KEY.get(key)
    return t.tooltip if t else ""


def header(label: str, key: str) -> str:
    """표 헤더용 — 이름 옆에 ⓘ 를 붙이고 hover 로 설명을 띄운다."""
    t = BY_KEY.get(key)
    if t is None:
        return escape(label)
    return f'{escape(label)}<span class="gi" title="{escape(t.tooltip)}">ⓘ</span>'


CSS = """
.gi{display:inline-block;margin-left:.25rem;font-size:.72em;color:var(--acc);
 cursor:help;font-weight:400;vertical-align:1px}
details.gloss{background:var(--card);border:1px solid var(--line2);border-radius:10px;
 margin-bottom:1rem}
details.gloss>summary{cursor:pointer;padding:.85rem 1.1rem;font-weight:700;font-size:.9rem;
 color:var(--acc);list-style:none;display:flex;align-items:center;gap:.5rem}
details.gloss>summary::-webkit-details-marker{display:none}
details.gloss>summary::before{content:"▸";font-size:.8rem}
details.gloss[open]>summary::before{content:"▾"}
details.gloss[open]>summary{border-bottom:1px solid var(--line2)}
.gloss-body{padding:1rem 1.1rem 1.15rem;display:flex;flex-direction:column;gap:.9rem}
.gloss-intro{font-size:.88rem;line-height:1.8;color:var(--fg2)}
.gterms{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fit,minmax(285px,1fr))}
.gterm{background:var(--card2);border-radius:9px;padding:.8rem .95rem;font-size:.83rem;
 line-height:1.7;display:flex;flex-direction:column;gap:.35rem}
.gterm .gname{font-weight:700;font-size:.88rem;color:var(--fg)}
.gterm .gread{color:var(--fg2)}
.gterm .gcau{color:var(--warn);font-size:.79rem;border-top:1px dashed var(--line);
 padding-top:.35rem;margin-top:.1rem}
"""


def _bold(text: str) -> str:
    """해설 안의 **강조** 만 굵게. escape 뒤에 치환해 주입을 막는다."""
    out, parts = "", escape(text).split("**")
    for i, p in enumerate(parts):
        out += f"<strong>{p}</strong>" if i % 2 else p
    return out


def panel(keys: list[str] | None = None, *, open_by_default: bool = False) -> str:
    """접이식 해설 패널. `keys` 를 주면 그 항목만, 없으면 전부."""
    terms = [BY_KEY[k] for k in keys if k in BY_KEY] if keys else list(TERMS)
    if not terms:
        return ""
    cards = "".join(
        f'<div class="gterm"><span class="gname">{escape(t.label)}</span>'
        f'<span>{_bold(t.one_line)}</span>'
        f'<span class="gread">{_bold(t.how_to_read)}</span>'
        + (f'<span class="gcau">{_bold(t.caution)}</span>' if t.caution else "")
        + "</div>" for t in terms)
    return (f'<details class="gloss"{" open" if open_by_default else ""}>'
            f'<summary>이 숫자들을 어떻게 읽나요? — 처음 보시면 펼쳐 보세요</summary>'
            f'<div class="gloss-body"><p class="gloss-intro">{_bold(INTRO)}</p>'
            f'<div class="gterms">{cards}</div></div></details>')
