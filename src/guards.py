from pathlib import Path
from typing import Any

import openpyxl


def check_xlsx_file(xlsx_path: Path, guards_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify file size, ZIP/XLSX signature (PK), and expected header row."""
    results: list[dict[str, Any]] = []
    min_bytes = int(guards_cfg.get("min_file_bytes", 50000))

    if not xlsx_path.exists():
        results.append({"name": "file_signature", "value": "missing", "limit": "PK", "ok": False})
        return results

    size = xlsx_path.stat().st_size
    head = xlsx_path.read_bytes()[:4]
    is_zip = head.startswith(b"PK")
    results.append(
        {
            "name": "file_signature",
            "value": f"size={size}, zip={is_zip}",
            "limit": f">={min_bytes} & PK",
            "ok": bool(is_zip and size >= min_bytes),
        }
    )
    if not (is_zip and size >= min_bytes):
        return results

    header_found = False
    wb = None
    try:
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        ws = wb.active
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > 20:
                break
            cells = [str(c or "").strip().lower() for c in row[:4]]
            if len(cells) >= 2 and "населений пункт" in cells[0] and "вулиця" in cells[1]:
                header_found = True
                break
    except Exception:
        header_found = False
    finally:
        if wb is not None:
            wb.close()

    results.append({"name": "header", "value": header_found, "limit": True, "ok": header_found})
    return results


def check_parsed_and_sync(
    *,
    profile: dict[str, Any],
    matched_settlements: int,
    records_count: int,
    prev_records_count: int | None,
    changed_addresses: int,
    total_active_addresses: int,
    review_count: int,
    accept_mass_change: bool = False,
) -> list[dict[str, Any]]:
    """Run quantitative guards on parsed records and sync diff."""
    guards_cfg = profile.get("guards", {})
    total_settlements = max(len(profile.get("settlements", [])), 1)
    results: list[dict[str, Any]] = []

    # 1. min_settlements_ratio
    sett_ratio = round(matched_settlements / total_settlements, 4)
    min_sett = float(guards_cfg.get("min_settlements_ratio", 0.9))
    results.append(
        {
            "name": "min_settlements_ratio",
            "value": sett_ratio,
            "limit": min_sett,
            "ok": sett_ratio >= min_sett,
        }
    )

    # 2. min_records_ratio_vs_prev
    min_rec = float(guards_cfg.get("min_records_ratio_vs_prev", 0.7))
    if prev_records_count and prev_records_count > 0:
        rec_ratio = round(records_count / prev_records_count, 4)
        results.append(
            {
                "name": "min_records_ratio_vs_prev",
                "value": rec_ratio,
                "limit": min_rec,
                "ok": rec_ratio >= min_rec,
            }
        )
    else:
        results.append(
            {
                "name": "min_records_ratio_vs_prev",
                "value": 1.0,
                "limit": min_rec,
                "ok": True,
            }
        )

    # 3. max_changed_ratio
    max_changed = float(guards_cfg.get("max_changed_ratio", 0.3))
    changed_ratio = round(changed_addresses / max(total_active_addresses, 1), 4)
    results.append(
        {
            "name": "max_changed_ratio",
            "value": changed_ratio,
            "limit": max_changed,
            "ok": True if accept_mass_change else (changed_ratio <= max_changed),
        }
    )

    # 4. max_review_ratio
    max_review = float(guards_cfg.get("max_review_ratio", 0.05))
    review_ratio = round(review_count / max(total_active_addresses + review_count, 1), 4)
    results.append(
        {
            "name": "max_review_ratio",
            "value": review_ratio,
            "limit": max_review,
            "ok": review_ratio <= max_review,
        }
    )

    return results


def all_guards_ok(results: list[dict[str, Any]]) -> bool:
    return all(r.get("ok", False) for r in results)


def first_failed_guard(results: list[dict[str, Any]]) -> str | None:
    for r in results:
        if not r.get("ok", False):
            return str(r.get("name", "unknown"))
    return None
