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
python3 -m src.pipelines.dashboard              # 브리핑 + 콕핏 + dashboard/index.html
python3 -m src.pipelines.dashboard --public     # 개인정보 제외본 → dashboard/public.html
```

한 번에 생성되는 것:
- `reports/daily/<날짜>-brief.md`
- `reports/cockpit/<날짜>-cockpit.md`
- `dashboard/index.html` (단일 현재 상태, 덮어쓰기)

## 2. 보유 현황이 바뀌었으면 편집기를 먼저

```bash
python3 -m src.pipelines.editor      # http://127.0.0.1:8765
```

편집기는 저장할 때마다 자동으로 스냅샷을 남기고 대시보드를 재생성한다.
직접 YAML 을 고쳤다면 스냅샷이 안 남으므로 수익 기여도 계산이 밀린다.

## 3. 캐시 정책

`data/` 는 **통째로 지워도 안전하다.** 다시 받는다.
강제 갱신이 필요하면 해당 호스트 디렉터리만 지운다:

```bash
rm -rf data/cache/openapi.tossinvest.com    # 시세만 강제 갱신
rm -rf data/cache/data.sec.gov              # SEC 재무 강제 갱신
```

TTL: 시세 60초 · 환율 10분 · 랭킹 5분 · SEC 재무 24시간 · 종목명 1주 · 공시 원문 영구.

## 4. 발행 전 렌더링 실측 검증

```bash
python3 -m http.server 8765 --directory dashboard
```

레이아웃 깨짐·가로 스크롤·다크모드 대비를 눈으로 확인한 뒤 서버를 종료한다.
로컬 파일을 그냥 열어도 된다 — 외부 리소스가 0개라 오프라인에서 동작한다.

## 5. 공개본을 만들 때

`--public` 은 다음을 제외한다:
- 보유 종목 목록·수량·평가액
- 콕핏의 평가액과 종목별 룩스루 내역
- **관심 종목 섹션 전체**와 거기서 파생된 액션 신호

유지되는 것: 증시 현황·매크로·주요 종목 랭킹(공개 시장 데이터)·집중도 지표(비율만).

공개본을 어딘가에 올릴 거라면 **올리기 전에 직접 열어보고 개인 정보가 없는지 확인한다.**
되돌리기 어려운 행위다.

## 6. 갱신 요약을 보고한다

무엇이 새로 들어왔고 무엇이 여전히 `확인 필요` 인지 항상 함께 보고한다.
**미확보 항목이 조용히 사라지면 안 된다.**

```
갱신 완료 — <날짜>
  한국 지수 2 · 미국 대용치 4 · 보유 N · 랭킹 6/6 · 신호 M
  여전히 미확보: 어닝콜 트랜스크립트, 테마 뉴스(3차 출처라 자동 수집 안 함)
  산출물: reports/daily/... · reports/cockpit/... · dashboard/index.html
```

## 모듈
`pipelines/{dashboard,daily_brief,cockpit,editor}` · `render/dashboard` · `provenance.record`
