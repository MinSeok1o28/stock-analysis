---
name: dashboard-refresh
description: 대시보드 갱신 — "갱신해줘" 한마디로 브리핑·콕핏·HTML 대시보드를 최신화한다. "갱신해줘", "대시보드 업데이트", "새로고침" 같은 요청에 사용한다. 자동 스케줄로 돌지 않고 호출할 때만 실행되는 반자동 방식이다.
---

# 대시보드 갱신

**자동 실행하지 않는다.** 호출할 때만 돈다 — 별도 API 청구를 피하고 구독 한도 안에서만
쓰기 위한 의도된 설계다. cron·GitHub Actions 를 붙이지 말 것
(Actions 는 키를 클라우드 secrets 에 넣어야 해서 "자격증명을 다루지 않는다" 와 충돌한다).

## 트리거
`갱신해줘` · `대시보드 업데이트` · `새로고침`

## 1. 실행

```bash
python3 -m src.pipelines.dashboard              # 브리핑 + 콕핏 + 이벤트 + dashboard/index.html
python3 -m src.pipelines.dashboard --public     # 개인정보 제외본 → dashboard/public.html
```

한 번에 생성되는 것:
- `reports/daily/<날짜>-brief.md`
- `reports/cockpit/<날짜>-cockpit.md`
- `reports/events/<날짜>-events.md`
- `dashboard/index.html` (단일 현재 상태, 덮어쓰기)
- `dashboard/stocks/<티커>.html` — 이벤트 상위 종목 중 없는 것만 자동 생성

## 1-1. 화면 구성 — 좌측 목차 7개

한 장에 다 쌓으면 스크롤이 길어져 뒤쪽을 아무도 안 본다. 목차에서 고른 하나만 뜬다.

| 뷰 | 내용 |
|---|---|
| 오늘 | 증시 현황·매크로·환율·확인 필요 |
| 주요 기업 | 한국 시가총액 상위 / 미국 S&P 500 편입 비중 상위 — **체크박스** |
| 이벤트 | 지금 볼 이유가 있는 종목 (한국·미국 분리) |
| 액션 신호 | 어느 딥다이브를 돌릴지 |
| 시장 랭킹 | 거래대금·급등·급락 (한/미 각 3종) |
| 보유·포트폴리오 | 밤사이 움직임 + 콕핏 |
| 관심 종목 | 실적 캘린더 |

선택은 URL 해시에 남아 북마크·새로고침에 견딘다 (`#events`).
**섹션은 숨겨질 뿐 지워지지 않는다** — 여러 섹션에서 체크한 종목이 배치 바구니에 함께 담긴다.

## 2. 보유 현황이 바뀌었으면 편집기를 먼저

```bash
python3 -m src.pipelines.editor      # http://127.0.0.1:8765
```

편집기는 저장할 때마다 자동으로 스냅샷을 남기고 대시보드를 재생성한다.
직접 YAML 을 고쳤다면 스냅샷이 안 남으므로 수익 기여도 계산이 밀린다.

## 2-1. 검색·배치 분석은 로컬 서버가 필요하다

```bash
python3 -m src.pipelines.serve                   # http://127.0.0.1:8766
BATCH_WORKERS=6 python3 -m src.pipelines.serve   # 배치 동시 실행 수 (기본 4, 최대 8)
```

`dashboard/index.html` 을 **파일로 직접 열면** 표·지표는 그대로 보이지만
검색·분석 생성·배치는 꺼진다. `file://` 에서는 `/api/*` 요청이 갈 곳이 없기 때문이다.
화면이 로드 시점에 이를 판별해 입력창을 끄고 이유를 적는다.

배치는 종목을 체크해 모아 **동시에** 생성하고 비교 페이지(`/compare?j=…`)로 연다.
순차로 돌리면 이득이 없다 — 동시 실행이라야 총 대기가 준다
(실측 2026-08-25: 단건 100초 · 3종목 동시 119초).
**비교 페이지는 메모리에만 있다.** 서버를 재시작하면 사라지고, 개별 종목 페이지는 파일로 남는다.

## 3. 캐시 정책

`data/` 는 **통째로 지워도 안전하다.** 다시 받는다.
강제 갱신이 필요하면 해당 호스트 디렉터리만 지운다:

```bash
rm -rf data/cache/openapi.tossinvest.com    # 시세만 강제 갱신
rm -rf data/cache/data.sec.gov              # SEC 재무 강제 갱신
```

실제 TTL (코드에서 확인한 값):

| 대상 | TTL | 위치 |
|---|---|---|
| 시세·국내지수 | 60초 | `toss.prices` · `market_indicators` |
| 랭킹 | 5분 | `toss.rankings` |
| 환율(토스) | 10분 | `toss.exchange_rate` |
| 일봉 | 1시간 (페이지드 12시간) | `toss.daily_candles(_paged)` |
| 종목정보·마스터 | 7일 | `toss.stock_info` · `universe` |
| SEC companyfacts | 7일 | `sec_edgar.annual_series` |
| SEC submissions | 12시간 | `sec_edgar.annual_filings` · `earnings_events` |
| DART 기업행위 공시 | 6시간 | `open_dart.corporate_actions` |
| DART 재무·공시목록 | 1일 | `open_dart` |
| DART corpCode.xml | 30일 | `open_dart.corp_index` |
| 10-K 본문 | 1년 | `_http.get_text` |

## 4. 발행 전 렌더링 실측 검증

```bash
python3 -m src.pipelines.serve      # http://127.0.0.1:8766
```

**`python3 -m http.server` 를 쓰지 않는다.** 정적 서버로는 `/api/*` 가 없어 검색·배치가
동작하지 않고, 8765 는 편집기 포트라 충돌한다.

레이아웃 깨짐·가로 스크롤·다크모드 대비를 눈으로 확인한다.
외부 리소스는 웹폰트 하나뿐이라 오프라인에서도 폰트만 시스템 폰트로 떨어지고 나머지는 동작한다.

## 5. 공개본을 만들 때

`--public` 은 다음을 제외한다:
- 보유 종목 목록·수량·평가액
- 콕핏의 평가액과 종목별 룩스루 내역
- **관심 종목 섹션 전체**와 거기서 파생된 액션 신호

유지되는 것: 증시 현황·매크로·주요 기업 목록·시장 랭킹(공개 시장 데이터)·집중도 지표(비율만).

목차에서 **관심 종목 뷰는 통째로 사라지고**, 보유 목록이 빠져 콕핏만 남으므로
뷰 이름이 `보유·포트폴리오` → `포트폴리오` 로 바뀐다. 이름이 내용과 어긋나면 안 된다.

공개본을 어딘가에 올릴 거라면 **올리기 전에 직접 열어보고 개인 정보가 없는지 확인한다.**
되돌리기 어려운 행위다.

## 6. 갱신 요약을 보고한다

무엇이 새로 들어왔고 무엇이 여전히 `확인 필요` 인지 항상 함께 보고한다.
**미확보 항목이 조용히 사라지면 안 된다.**

```
갱신 완료 — <날짜>
  한국 지수 2 · 미국 대용치 4 · 보유 N · 랭킹 6/6 · 신호 M · 이벤트 종목 K
  급등락 이상치: 등락률을 그대로 읽을 수 없는 종목 J개 (있으면 티커까지)
  여전히 미확보: 어닝콜 트랜스크립트, 테마 뉴스(3차 출처라 자동 수집 안 함)
  산출물: reports/daily/... · reports/cockpit/... · reports/events/... · dashboard/index.html
```

## 모듈
`pipelines/{dashboard,daily_brief,cockpit,event_scanner,majors,editor,serve,compare}` ·
`render/{dashboard,glossary}` · `provenance.record`
