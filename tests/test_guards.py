import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guards import all_guards_ok, check_parsed_and_sync, check_xlsx_file, first_failed_guard


def _dummy_profile(n_settlements: int = 67) -> dict:
    return {
        "settlements": [{"name": f"S{i}"} for i in range(n_settlements)],
        "guards": {
            "min_file_bytes": 50000,
            "min_settlements_ratio": 0.9,
            "min_records_ratio_vs_prev": 0.7,
            "max_changed_ratio": 0.3,
            "max_review_ratio": 0.05,
        },
    }


def test_guard_empty_or_html_file(tmp_path: Path):
    html_file = tmp_path / "bad.xlsx"
    html_file.write_text("<html>error</html>", encoding="utf-8")
    res = check_xlsx_file(html_file, {"min_file_bytes": 50000})
    assert not all_guards_ok(res)
    assert first_failed_guard(res) == "file_signature"


def test_guard_settlements_ratio():
    res = check_parsed_and_sync(
        profile=_dummy_profile(67),
        matched_settlements=30,
        records_count=860,
        prev_records_count=860,
        changed_addresses=0,
        total_active_addresses=15000,
        review_count=0,
    )
    assert not all_guards_ok(res)
    assert first_failed_guard(res) == "min_settlements_ratio"


def test_guard_records_ratio():
    res = check_parsed_and_sync(
        profile=_dummy_profile(67),
        matched_settlements=67,
        records_count=340,
        prev_records_count=860,
        changed_addresses=0,
        total_active_addresses=15000,
        review_count=0,
    )
    assert not all_guards_ok(res)
    assert first_failed_guard(res) == "min_records_ratio_vs_prev"


def test_guard_mass_change_and_accept_flag():
    res_fail = check_parsed_and_sync(
        profile=_dummy_profile(67),
        matched_settlements=67,
        records_count=860,
        prev_records_count=860,
        changed_addresses=7500,
        total_active_addresses=15000,
        review_count=0,
        accept_mass_change=False,
    )
    assert not all_guards_ok(res_fail)
    assert first_failed_guard(res_fail) == "max_changed_ratio"

    res_ok = check_parsed_and_sync(
        profile=_dummy_profile(67),
        matched_settlements=67,
        records_count=860,
        prev_records_count=860,
        changed_addresses=7500,
        total_active_addresses=15000,
        review_count=0,
        accept_mass_change=True,
    )
    assert all_guards_ok(res_ok)


def test_guard_review_ratio():
    res = check_parsed_and_sync(
        profile=_dummy_profile(67),
        matched_settlements=67,
        records_count=860,
        prev_records_count=860,
        changed_addresses=0,
        total_active_addresses=1000,
        review_count=100,
    )
    assert not all_guards_ok(res)
    assert first_failed_guard(res) == "max_review_ratio"
