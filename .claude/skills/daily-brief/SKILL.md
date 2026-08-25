---
name: daily-brief
description: 일일 브리핑 — 오늘 뭘 봐야 하는지 한 화면으로. "오늘 브리핑", "브리핑 돌려줘", "오늘 뭐 봐야 해", "밤사이 어땠어" 같은 요청에 사용한다. 한국·미국 증시, 매크로·환율, 보유 종목 움직임, 시장 주요 종목, 실적 임박을 정리하고 어느 딥다이브를 돌릴지 신호를 낸다.
---

# 일일 브리핑

이 시스템의 오케스트레이터. **결론을 내리지 않는다. 어디를 더 파야 하는지만 짚는다.**

## 트리거
`오늘 브리핑` · `브리핑 돌려줘` · `오늘 뭐 봐야 해` · `밤사이 어땠어`

## 1. 파이프라인 실행

```bash
python3 -m src.pipelines.daily_brief      # 리포트만
python3 -m src.pipelines.dashboard        # 브리핑 + 콕핏 + HTML 대시보드
```

`reports/daily/<날짜>-brief.md` 생성. 대시보드까지 원하면 두 번째를 쓴다.

## 2. 브리핑이 모으는 것

| 구획 | 출처 | 주의 |
|---|---|---|
| 한국 증시 | 토스 `market-indicators` (KOSPI·KOSDAQ) | — |
| 미국 증시 | 토스 시세 — **SPY·QQQ·DIA·IWM 대용치** | 지수 자체가 아님을 표기 |
| 매크로 | FRED (미10년물·2년물·연방기금) | 관측일이 며칠 전일 수 있음 |
| 환율 | 토스(장중) + Frankfurter(ECB 영업일 종가) | 둘의 성격이 다름 |
| 보유 종목 | `holdings.yaml` + 토스 시세·일봉 | 변동률은 종목당 캔들 1콜 |
| 주요 종목 | 토스 `rankings` — 한/미 각각 거래대금·급등·급락 **6종** | 보유 외 시장 전체 |
| 급등락 이상치 | 일봉 대조(`core/anomalies`) + DART 공시(`open_dart.corporate_actions`) | 아래 2-1 |
| 관심 종목 | `watchlist.yaml` (수동) | 실적일이 있어야 신호가 난다 |

## 2-1. 급등락 등락률을 그대로 싣지 않는다

랭킹에는 **직전 종가가 비교 대상이 못 되는 종목**이 섞여 들어온다.
거래정지 후 재개, 신규상장, 액면분할·병합이 그렇다.

정황과 확정을 분리한다:

1. **정황** — `core/anomalies.inspect()` 가 일봉만 보고 판단한다. ±20% 미만은 검사하지 않고,
   극단 변동 상위 6종목만 본다 (API 호출 절약).
   - `거래정지 후 재개` — 무거래·종가고정 3세션 이상
   - `이력 부족` — 일봉 2개 미만 (신규상장)
   - `액면분할·병합 정황` — 2·2.5·3·4·5·10·20·50·100:1 배수에 3% 이내 근접
   - `랭킹 기준가와 일봉 불일치`
2. **확정** — 한국 종목만 `open_dart.corporate_actions()` 로 공시를 찾아 원인을 못박는다.
   미국은 SEC 에 DART `list.json` 대응물이 없어 **정황까지만** 가고 그 한계를 산출물에 적는다.

**정황만 보고 원인을 단정하면 틀린다.** 실제 사례 (2026-08-25):
- `096610 알에프세미 -95.34%` — 액면분할로 짐작했으나 공시 확인 결과 **상장폐지 정리매매**
- `900270 헝셩그룹 +30.00%` — **주식병합(무액면주식) 주권 변경상장**

`설명되지 않는 극단 변동`은 ⚠ 를 달지 않는다. '검사했고 인위적 요인이 없었다'는 결과라
경고로 띄우면 급등 상위가 전부 ⚠ 로 도배돼 진짜 경고가 묻힌다.
대신 검사가 돌았다는 사실을 `확인 필요` 에 남긴다.

## 3. 신호 규칙 — 임계값은 이 파일에서 바꾼다

| 조건 | 신호 |
|---|---|
| 실적 발표 7일 이내 | `RUN_STORY_READER` |
| 밤사이 ±5% 이상 이동 | `RUN_PRICE_DECODER` |
| 해외 비중 70% 초과 | `CHECK_FX_EXPOSURE` |
| 필수 데이터 미확보 | `DATA_GAP` |
| 랭킹 등락률을 그대로 읽을 수 없음 | `DATA_GAP` (티커 지정 · 정황과 공시를 근거로 첨부) |

숫자를 바꾸려면 `src/pipelines/daily_brief.py` 상단 상수만 고친다
(`MOVE_THRESHOLD`, `EARNINGS_WINDOW`, `FOREIGN_HEAVY`, `ANOMALY_MAX_LOOKUPS`).
이상치 임계값은 `src/core/anomalies.py` 상단에 있다
(`EXTREME_MOVE`, `HALT_MIN_SESSIONS`, `SPLIT_RATIOS`). 소스·계산 계층은 건드리지 않는다.

**`SignalKind` 에 매수·매도 항목이 없다.** 구조적으로 매매 신호를 낼 수 없고,
`Signal.reason` 에 매매 표현을 쓰면 `ValueError` 가 난다.

## 4. 실적일의 확정/추정을 구분한다

무료 실적 캘린더 소스가 없어 `watchlist.yaml` 에 사람이 적는다.

```yaml
- ticker: NVDA
  earnings_date: 2026-08-26
  earnings_confirmed: true                    # 회사 공식 발표
  earnings_source: NVIDIA IR
```

`earnings_confirmed: false` 면 신호에 `(추정)` 이 붙고
`확인 필요` 에 "회사 IR 확인 권장" 이 자동으로 올라온다.
출처마다 날짜가 다른 경우(MSFT 는 10/27·10/28·11/4 로 상이) 그 사실을 `earnings_source` 에 적는다.

## 5. 테마 뉴스는 파이프라인이 수집하지 않는다

웹검색은 **3차 출처**라 수치를 만들 수 없다. 의도된 설계다.
뉴스가 필요하면 WebSearch 로 직접 찾아 `websearch.note()` 로 감싸 정성 관찰로 추가하고,
**거기서 나온 숫자를 브리핑 수치 칸에 넣지 않는다.**

## 6. 출력

리포트를 그대로 보여준다. `## 오늘의 액션 신호` 가 핵심이다.
브리핑 자체가 결론을 내지 않고, 어느 딥다이브(`story-reader`·`price-decoder`·
`company-decoder`)를 돌릴지만 제시한다. 신호를 받아 이어서 실행할 수 있다.

## 모듈
`pipelines/{daily_brief,dashboard}` · `core/anomalies` ·
`sources/{toss,fred,frankfurter,prices,open_dart,websearch}` ·
`models.{Signal,Market}` · `portfolio_io`
