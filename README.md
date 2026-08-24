# stock-analysis

개인 투자 리서치 시스템. **리서치·정리·계산·모니터링만 한다. 매매 판단은 하지 않는다.**

클론해서 API 키만 넣으면 각자 로컬에서 돌아갑니다.
보유 종목·산출물은 이 저장소에 올라가지 않습니다 — 전부 로컬에만 남습니다.

> 원형이 된 방법론: [Claude Code로 개인 투자 리서치 자동화한 방법](https://puzzle-voice-64f.notion.site/Claude-Code-3b45e7fa3f8f8082beb9ed90439e88c7)
> 이 저장소는 그 구조를 코드로 구현하고, 웹검색 대신 1차 출처(SEC·FRED·ECB)에 직접 연결한 것입니다.

## 무엇을 하나

- **일일 브리핑** — 한국·미국 증시, 매크로·환율, 보유 종목 밤사이 움직임, 시장 주요 종목, 실적 임박
- **포트폴리오 콕핏** — 집중도(HHI), **ETF 경유 숨은 중복 노출**, 자산유형별 평가 잣대, 환노출 시나리오
- **가격 판독기** — 역DCF로 "이 가격이 요구하는 성장률"을 이분법으로 역산 + WACC 민감도
- **스토리 리더** — 연도별 공시 문구 비교 (신규/삭제 문장, `will→may` 톤다운, 헤지 어휘 증감)
- **액션 신호** — 매수·매도가 아니라 **어느 딥다이브를 돌릴지**만 제시

## 빠른 시작

```bash
git clone https://github.com/<you>/stock-analysis.git && cd stock-analysis
pip install requests PyYAML Jinja2          # 의존성 3개가 전부
cp .env.example .env                        # SEC_USER_AGENT 만 채워도 동작
cp portfolio/holdings.example.yaml portfolio/holdings.yaml
cp portfolio/watchlist.example.yaml portfolio/watchlist.yaml

python3 -m src.config                       # 무엇이 되고 안 되는지 진단
python3 -m unittest discover -s tests -t . -q   # 89개 테스트
python3 -m src.pipelines.dashboard          # → dashboard/index.html
```

브라우저로 `dashboard/index.html` 을 열면 끝입니다. 서버가 필요 없습니다.

```bash
python3 -m src.pipelines.editor             # 포트폴리오 편집 UI (127.0.0.1:8765)
python3 -m src.pipelines.dashboard --public # 개인 정보 뺀 공개용 → dashboard/public.html
```

## API 키

| 변수 | 용도 | 비용 | 필수 |
|---|---|---|---|
| `SEC_USER_AGENT` | SEC EDGAR 가 요구하는 연락처 (키 아님) | — | **예** |
| `FRED_API_KEY` | 미국 금리·매크로 · [발급](https://fred.stlouisfed.org/docs/api/api_key.html) | 무료 | 권장 |
| `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` | 시세·지수·랭킹·환율 (한국+미국) | 무료 | 권장 |
| `OPENDART_API_KEY` | 한국 공시·재무 · [발급](https://opendart.fss.or.kr/) | 무료 | 미구현 |
| `FMP_API_KEY` | 어닝콜 트랜스크립트 | 유료 | 선택 |

키가 없으면 해당 소스만 `확인 필요`로 표기되고 나머지는 정상 동작합니다.

**토스증권**: WTS > 설정 > Open API 에서 발급하고, 같은 화면 하단 **허용 IP 관리**에
공인 IP를 등록해야 합니다 (`curl -s ifconfig.me`).

## 데이터 출처와 등급

| 등급 | 정의 | 소스 | 수치 사용 |
|---|---|---|---|
| **1차** | 발행 주체가 직접 배포 | SEC EDGAR(XBRL·10-K), FRED, ECB(Frankfurter), ETF 발행사 | 허용 |
| **2차** | 벤더가 정규화·집계 | 토스증권 Open API | 허용 |
| **3차** | 비공식 스크래핑·웹검색 | 웹검색 | **금지 — 정성 전용** |

3차 출처로 수치를 만들려 하면 `provenance.py` 가 예외를 던집니다.

## 계층

```
models  ←  sources    외부 I/O. 벤더가 바뀌면 여기만 바뀐다
   ↑
   ├────  core        순수 계산. I/O 금지. 네트워크 없이 테스트된다
   ↑
   ├────  render      표현
   ↑
   └────  pipelines   조율. 이 계층만 셋 모두를 안다
```

**의존은 항상 안쪽으로만.** `tests/test_layering.py` 가 이를 검사합니다.

## 원칙이 코드로 강제되는 지점

이 프로젝트의 핵심입니다. 원칙을 프롬프트에 적지 않고 실행 단계에서 막습니다.

| 원칙 | 구현 | 위반 시 |
|---|---|---|
| 출처 없는 숫자 금지 | `provenance.require_sourced()` | 렌더 단계 예외 |
| 웹검색에 페이지 번호 금지 | `Source.__post_init__` | 생성 단계 예외 |
| 3차 출처로 수치 금지 | `Sourced.__post_init__` | 생성 단계 예외 |
| 암산 금지 | `core/valuation/reverse_dcf.py` 이분법 + 수렴 진단 | — |
| 변화 없으면 "변화 없음" | `sentence_diff.SentenceDiff.is_material` | — |
| **매매 신호 금지** | `SignalKind` 에 매매 항목 부재 + `Signal` 이 매매 표현 거부 | `ValueError` |
| 브로커 주문 경로 차단 | `toss.ALLOWED_PATHS` + 계좌 헤더 미생성 | `OrderPathBlocked` |
| 계층 의존 방향 | `tests/test_layering.py` | 테스트 실패 |

### 브로커 API 안전 경계

토스 자격증명은 주문 권한도 갖지만, 이 코드로는 주문을 낼 수 없습니다.

1. `ALLOWED_PATHS` 화이트리스트 — 시세·종목·환율·랭킹 경로만
2. **`X-Tossinvest-Account` 헤더를 생성하는 코드가 없음** — 토스는 계좌·주문 API에 이 헤더를
   요구하므로, 헤더가 없으면 그 API는 애초에 동작하지 않습니다
3. GET 전용 (POST는 토큰 발급 1건)

`tests/test_layering.py::TestBrokerBoundary` 5개 테스트가 검사합니다.

## Claude Code 스킬

`.claude/skills/` 에 6개 스킬이 있습니다. Claude Code 에서 트리거 문구로 호출합니다.

| 스킬 | 트리거 | 계층 |
|---|---|---|
| `company-decoder` | `AAPL 분석해줘` | 2층 |
| `story-reader` | `AAPL 스토리 분석해줘` | 2층 |
| `price-decoder` | `AAPL 지금 사도 되나` | 2층 |
| `portfolio-cockpit` | `포트폴리오 봐줘` | 1.5층 |
| `daily-brief` | `오늘 브리핑` | 1층 |
| `dashboard-refresh` | `갱신해줘` | 1층 |

1층이 신호를 내고 2층이 그 신호를 받아 깊게 들어갑니다.
운영 규칙은 [`CLAUDE.md`](CLAUDE.md) 에 있고 모든 스킬에 자동 적용됩니다.

## 구현 현황

| 소스 | 상태 |
|---|---|
| SEC EDGAR (재무 XBRL) | 동작 · 키 불필요 |
| Frankfurter (ECB 환율) | 동작 · 키 불필요 |
| ETF 구성종목 | 동작 · SPDR 자동 / 타 발행사는 CSV 수동 공급 |
| FRED (매크로) | 동작 · 무료 키 |
| 토스증권 (시세·지수·랭킹) | 동작 · 무료 키 |
| 10-K 본문 다운로드 | **미구현** — 스토리 리더가 대기 중 |
| OpenDART (한국 공시) | 미구현 |
| 어닝콜 트랜스크립트 | 미구현 (무료 소스 없음) |
| 실적 캘린더 | 무료 소스 없음 → `watchlist.yaml` 수동 |

## 한계

- 실시간 계좌 연동을 하지 않습니다. 보유 현황은 수동 갱신합니다.
- 자동 스케줄 실행을 하지 않습니다. 호출할 때만 돕니다.
- 한국 상장사는 어닝콜 전문 공개가 드물어 스토리 분석 정밀도가 낮습니다 —
  시스템이 산출물에 스스로 명시합니다.
- **이 시스템의 어떤 결론도 투자 자문이 아닙니다.** 1차 스크리너로만 쓰고,
  판단에 직접 쓰는 숫자는 원문에서 재확인하십시오.

## 라이선스

MIT
