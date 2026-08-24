"""설정 로딩. `.env` 를 읽어 os.environ 에 채운다.

stdlib만 쓴다 (python-dotenv 의존성 추가 없이). sources/_http.py 가 import 시 자동 실행하므로
스킬이나 스크립트에서 따로 부를 필요가 없다.

이미 셸에 설정된 환경변수를 덮어쓰지 않는다 — CI·일회성 실행이 .env 를 이길 수 있게.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(os.environ.get("ENV_FILE", ".env"))

#: (환경변수, 설명, 필수 여부, 발급처)
KEYS: tuple[tuple[str, str, bool, str], ...] = (
    ("SEC_USER_AGENT", "SEC EDGAR 연락처 (키 아님)", True,
     "예: \"stock-analysis your@email.com\" — SEC가 요구하는 식별자"),
    ("TOSS_CLIENT_ID", "토스증권 시세·환율", False,
     "토스증권 WTS > 설정 > Open API (+ 허용 IP 등록 필수)"),
    ("TOSS_CLIENT_SECRET", "토스증권 시세·환율", False,
     "위와 같은 화면"),
    ("FRED_API_KEY", "미국 매크로·금리", False,
     "https://fred.stlouisfed.org/docs/api/api_key.html"),
    ("OPENDART_API_KEY", "한국 공시·재무", False, "https://opendart.fss.or.kr/"),
    ("ECOS_API_KEY", "한국 매크로·환율", False, "https://ecos.bok.or.kr/api/"),
    ("FMP_API_KEY", "어닝콜 트랜스크립트·실적 캘린더", False,
     "https://site.financialmodelingprep.com/ (유료 기능)"),
)

_loaded = False


def load_env(path: Path | None = None, *, override: bool = False) -> int:
    """.env 를 파싱해 환경변수로 올린다. 채워진 개수를 반환한다."""
    global _loaded
    p = path or ENV_PATH
    if not p.exists():
        _loaded = True
        return 0
    n = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val[:1] == val[-1:] and val[:1] in ("'", '"'):
            val = val[1:-1]
        if not val:
            continue
        if override or key not in os.environ:
            os.environ[key] = val
            n += 1
    _loaded = True
    return n


def ensure_loaded() -> None:
    if not _loaded:
        load_env()


def status() -> list[tuple[str, str, bool, bool, str]]:
    """(변수, 설명, 필수, 설정됨, 발급처)"""
    ensure_loaded()
    return [(k, d, req, bool(os.environ.get(k, "").strip()), how) for k, d, req, how in KEYS]


def doctor() -> int:
    """설정 진단. 무엇이 되고 무엇이 안 되는지 한눈에."""
    ensure_loaded()
    exists = ENV_PATH.exists()
    print(f"설정 파일: {ENV_PATH.resolve()}  {'✓ 있음' if exists else '✗ 없음 — cp .env.example .env'}")
    print()
    missing_required = 0
    for k, desc, req, ok, how in status():
        mark = "✓" if ok else ("✗" if req else "·")
        tag = " [필수]" if req and not ok else ""
        print(f"  {mark} {k:22s} {desc}{tag}")
        if not ok:
            print(f"      → {how}")
            if req:
                missing_required += 1
    print()
    have_toss = all(os.environ.get(k, "").strip() for k in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"))
    print("지금 가능한 것:")
    print(f"  {'✓' if os.environ.get('SEC_USER_AGENT') else '✗'} 미국 재무·공시 (SEC, 무료)")
    print("  ✓ ECB 환율 (Frankfurter, 키 불필요)")
    print(f"  {'✓' if have_toss else '✗'} 시세·장중환율 (토스증권)")
    print(f"  {'✓' if os.environ.get('FRED_API_KEY') else '✗'} 매크로·무위험수익률 (FRED)")
    print("  ✗ 어닝콜 트랜스크립트 · 실적 캘린더 (소스 없음 → '확인 필요'로 표기)")
    return missing_required


if __name__ == "__main__":
    import sys
    sys.exit(1 if doctor() else 0)
