---
name: dashboard-refresh
description: 대시보드 갱신 — "갱신해줘" 한마디로 브리핑과 HTML 대시보드를 최신화한다. "갱신해줘", "대시보드 업데이트", "새로고침" 같은 요청에 사용한다. 자동 스케줄로 돌지 않고 호출할 때만 실행되는 반자동 방식이다.
---

# 대시보드 갱신

**자동 실행하지 않는다.** 호출할 때만 돈다 — 별도 API 청구를 피하고 구독 한도 안에서만 쓰기 위한
의도된 설계다. cron·스케줄러를 붙이지 말 것.

## 트리거
`갱신해줘` · `대시보드 업데이트` · `새로고침`

## 절차

1. **캐시 정책 확인** — `data/cache/`는 TTL이 지나면 자동 갱신된다.
   강제 갱신이 필요하면 해당 호스트 디렉터리만 지운다. `data/` 전체는 언제든 지워도 안전하다.
2. **일일 브리핑 재생성** — `daily-brief` 스킬 절차를 실행.
3. **콕핏 재생성** — 보유 현황이 바뀌었으면 `portfolio-cockpit` 실행.
   `portfolio/holdings.yaml`이 변경됐으면 먼저 `portfolio/snapshots/<날짜>.yaml`로 복사한다.
4. **HTML 렌더** — `render/dashboard.write()`. `dashboard/index.html`을 덮어쓴다.
   (reports/는 날짜별로 쌓고, dashboard/는 단일 현재 상태다.)
5. **렌더링 실측 검증** — 발행 전 반드시:
   ```bash
   python3 -m http.server 8765 --directory dashboard &
   ```
   레이아웃 깨짐·가로 스크롤·다크모드 대비를 확인한다. 확인 후 서버를 종료한다.
6. **원장 기록** — `provenance.record()`로 이번 갱신에 쓴 출처를 남긴다.

## 갱신 요약 보고
무엇이 새로 들어왔고 무엇이 여전히 `확인 필요`인지 항상 함께 보고한다.
미확보 항목이 조용히 사라지면 안 된다.

```
갱신 완료 — <날짜>
  새로 받은 출처: N건 (1차 X · 2차 Y)
  여전히 미확보: <항목> (<이유>)
  산출물: reports/daily/... · dashboard/index.html
```

## 사용 모듈
`render/dashboard` · `provenance.record` · (daily-brief, portfolio-cockpit 스킬 호출)
