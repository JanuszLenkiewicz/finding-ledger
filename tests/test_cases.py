from pathlib import Path

import pytest

from findingledger import alarms, case_for_signature, evaluate, graduate, load_cases


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    (tmp_path / "len-01.yaml").write_text(
        "id: len-01\nstatus: open\ncheck: LEN-01\nbacklog: LEN-01-length-drift\n"
        "since: 2026-08-08\nnotes: drift awaiting product decision\n", encoding="utf-8")
    (tmp_path / "fak-01.yaml").write_text(
        "id: fak-01\nstatus: regression\ncheck: FAK-01\nbacklog: FAK-01-university\n",
        encoding="utf-8")
    (tmp_path / "ton-01.yaml").write_text(
        "id: ton-01\nstatus: sanity\nrubric: |\n  Phrase allowed only in negation.\n",
        encoding="utf-8")
    return tmp_path


def test_load_and_lookup(cases_dir: Path):
    cases = load_cases(cases_dir)
    assert {c.id for c in cases} == {"len-01", "fak-01", "ton-01"}
    assert case_for_signature(cases, "FAK-01-university").id == "fak-01"


def test_tristate_semantics(cases_dir: Path):
    cases = load_cases(cases_dir)
    outcomes = {o.case.id: o for o in evaluate(cases, {"LEN-01": "FAIL", "FAK-01": "FAIL"})}
    assert outcomes["len-01"].action == "status-quo"        # open + FAIL = expected
    assert outcomes["fak-01"].action == "alarm"             # regression + FAIL = defect returned
    assert outcomes["ton-01"].action == "needs-judge"       # rubric with no result
    assert [o.case.id for o in alarms(list(outcomes.values()))] == ["fak-01"]


def test_open_pass_is_graduation_candidate(cases_dir: Path):
    cases = load_cases(cases_dir)
    outcomes = {o.case.id: o for o in evaluate(cases, {"LEN-01": "PASS"})}
    assert outcomes["len-01"].action == "graduation-candidate"


def test_graduate_flips_only_open(cases_dir: Path):
    case = graduate(cases_dir, "len-01")
    assert case.status == "regression"
    assert "status: regression" in (cases_dir / "len-01.yaml").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        graduate(cases_dir, "fak-01")


def test_invalid_case_rejected(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text("id: bad\nstatus: open\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one of check/rubric"):
        load_cases(tmp_path)
