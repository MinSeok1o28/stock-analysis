"""서사 근거 보고서 대조 검증. 네트워크 없이 돈다.

고치려는 문제: 서사에 `updated` 날짜만 있어서 **어느 보고서를 근거로 쓴 해석인지** 알 수 없었다.
날짜만으로는 낡음을 판단할 수 없다 — 90일이 지나도 새 보고서가 없으면 안 낡은 것이고,
30일밖에 안 지났어도 새 10-K 가 나왔으면 낡은 것이다.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.models import Market
from src.narrative_io import FilingBasis, Narrative, Risk, load, save
from src.pipelines.filings import BasisState, FilingRef, check_basis
from src.provenance import Unavailable

OLD = FilingRef("NVDA", Market.US, "10-K", date(2025, 2, 26),
                "0001045810-25-000023", "https://sec.gov/old")
NEW = FilingRef("NVDA", Market.US, "10-K", date(2026, 2, 25),
                "0001045810-26-000021", "https://sec.gov/new")


def narrative(basis: FilingBasis | None = None, **kw) -> Narrative:
    return Narrative("NVDA", date(2026, 8, 24), one_liner="엔비디아는 계산 장치를 판다.",
                     basis=basis or FilingBasis(), **kw)


class TestFilingRef(unittest.TestCase):
    def test_label_uses_filing_date_not_fiscal_year(self) -> None:
        """SEC 의 fiscal_year 는 제출일 연도라 실제 회계연도와 어긋난다 — 접수일로 적는다."""
        self.assertEqual(NEW.label, "10-K (2026-02-25 접수)")

    def test_to_basis_round_trip(self) -> None:
        b = NEW.to_basis()
        self.assertEqual(b.accession, NEW.accession)
        self.assertEqual(b.filed_on, NEW.filed_on)
        self.assertFalse(b.is_empty)


class TestCheckBasis(unittest.TestCase):
    def test_same_accession_is_current(self) -> None:
        c = check_basis(narrative(NEW.to_basis()), NEW)
        self.assertIs(c.state, BasisState.CURRENT)
        self.assertFalse(c.is_warning)

    def test_new_filing_makes_it_stale(self) -> None:
        """서사를 어제 썼어도 그 뒤 새 10-K 가 나왔으면 낡은 것이다."""
        c = check_basis(narrative(OLD.to_basis()), NEW)
        self.assertIs(c.state, BasisState.STALE)
        self.assertTrue(c.is_warning)
        self.assertIn("2025-02-26", c.detail)
        self.assertIn("2026-02-25", c.detail)

    def test_old_narrative_without_basis(self) -> None:
        """이 필드가 없던 시절의 서사. 재생성하면 기록된다고 알려준다."""
        c = check_basis(narrative(), NEW)
        self.assertIs(c.state, BasisState.UNRECORDED)
        self.assertTrue(c.is_warning)

    def test_lookup_failure_is_unknown_not_stale(self) -> None:
        """최신 조회에 실패한 것을 '낡음' 으로 단정하면 안 된다."""
        c = check_basis(narrative(NEW.to_basis()),
                        Unavailable("NVDA 10-K 목록", "SEC 응답 없음"))
        self.assertIs(c.state, BasisState.UNKNOWN)
        self.assertFalse(c.is_warning)

    def test_none_current_is_unknown(self) -> None:
        c = check_basis(narrative(NEW.to_basis()), None)
        self.assertIs(c.state, BasisState.UNKNOWN)

    def test_empty_narrative_has_nothing_to_compare(self) -> None:
        c = check_basis(Narrative("NVDA", None), NEW)
        self.assertIs(c.state, BasisState.UNKNOWN)
        self.assertFalse(c.is_warning)


class TestBasisPersistence(unittest.TestCase):
    """yaml 왕복. 기존 서사(based_on 없음)를 읽어도 깨지지 않아야 한다."""

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            n = Narrative("NVDA", date(2026, 8, 25), one_liner="한 줄",
                          risks=[Risk("리스크", "설명", "근거")],
                          basis=NEW.to_basis())
            save(n, out)
            got = load("NVDA", out)
            self.assertEqual(got.basis.accession, NEW.accession)
            self.assertEqual(got.basis.filed_on, date(2026, 2, 25))
            self.assertEqual(got.basis.form, "10-K")
            self.assertEqual(got.basis.url, "https://sec.gov/new")

    def test_legacy_yaml_without_based_on(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "NVDA.yaml").write_text(
                "ticker: NVDA\nupdated: 2026-08-24\none_liner: 한 줄\n", encoding="utf-8")
            got = load("NVDA", out)
            self.assertTrue(got.basis.is_empty)
            self.assertEqual(got.one_liner, "한 줄")

    def test_malformed_based_on_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "NVDA.yaml").write_text(
                "ticker: NVDA\nupdated: 2026-08-24\none_liner: 한 줄\n"
                "based_on:\n  form: 10-K\n  filed_on: 그날\n  accession: X-1\n",
                encoding="utf-8")
            got = load("NVDA", out)
            self.assertEqual(got.basis.accession, "X-1")
            self.assertIsNone(got.basis.filed_on)      # 못 읽은 날짜는 버린다
            self.assertFalse(got.basis.is_empty)

    def test_empty_basis_is_not_written(self) -> None:
        """근거가 없으면 빈 based_on 블록을 남기지 않는다 — 없는 것과 빈 것은 다르다."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            save(Narrative("NVDA", date(2026, 8, 25), one_liner="한 줄"), out)
            self.assertNotIn("based_on", (out / "NVDA.yaml").read_text(encoding="utf-8"))


class TestBasisLabel(unittest.TestCase):
    def test_empty_label(self) -> None:
        self.assertEqual(FilingBasis().label, "근거 보고서 미기록")

    def test_label_without_date(self) -> None:
        self.assertEqual(FilingBasis(form="10-K", accession="X").label, "10-K")

    def test_label_falls_back_when_form_missing(self) -> None:
        self.assertEqual(FilingBasis(accession="X").label, "정기보고서")


if __name__ == "__main__":
    unittest.main()
