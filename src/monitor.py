import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_output import build_all_outputs
from common.io import ROOT, append_jsonl, read_json, resolve_path, write_json_atomic
from guards import all_guards_ok, check_parsed_and_sync, check_xlsx_file, first_failed_guard
from sync_addresses import run_sync

LOCK_STALE_SECONDS = 3600


def acquire_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        age = time.time() - lock_path.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            return False
    lock_path.write_text(f"pid={os.getpid()} started={datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")
    return True


def release_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def run_step(script: str, profile: str, extra_args: list[str] | None = None) -> None:
    script_path = str(ROOT / script)
    cmd = [sys.executable, script_path, "--profile", profile]
    if extra_args:
        cmd.extend(extra_args)
    print(f"\n=== Running {script} ===")
    subprocess.run(cmd, check=True)


def write_run_report(report: dict[str, Any]) -> None:
    write_json_atomic(ROOT / "output" / "run_report.json", report)
    append_jsonl(ROOT / "data" / "reports" / "runs.jsonl", report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Orchestrator for AdresBazaPidcherg pipeline")
    parser.add_argument("--profile", default="profiles/hromada.json", help="Path to hromada profile JSON")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip network requests (use local cache)")
    parser.add_argument("--accept-mass-change", action="store_true", help="Allow mass subqueue changes (>max_changed_ratio)")
    parser.add_argument("--force-rebuild", action="store_true", help="Rebuild even if source sha256 is unchanged")
    args = parser.parse_args()

    lock_path = ROOT / "data" / "state" / ".lock"
    if not acquire_lock(lock_path):
        print("Another instance is already running (lock active). Exiting with code 5.")
        sys.exit(5)

    started_dt = datetime.now(timezone.utc)
    started_iso = started_dt.isoformat().replace("+00:00", "Z")
    run_id = started_dt.strftime("%Y%m%d-%H%M")

    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_iso,
        "finished_at": None,
        "status": "error",
        "source": {},
        "steps": {},
        "counts": {},
        "changes": {},
        "guards": [],
        "error": None,
    }

    try:
        profile_path = resolve_path(args.profile)
        if not profile_path.exists():
            raise FileNotFoundError(f"Profile not found: {profile_path}")

        profile = read_json(profile_path)
        hromada_id = profile["id"]
        state_path = ROOT / "data" / "state" / "addresses.json"
        prev_state = read_json(state_path) if state_path.exists() else {}
        prev_sha = prev_state.get("source_sha256", "")

        # 1. Fetch
        if not args.skip_fetch:
            run_step("src/fetch_oblenergo.py", str(profile_path))
            report["steps"]["fetch"] = "ok"
        else:
            report["steps"]["fetch"] = "skipped"

        meta_path = ROOT / "data" / "raw_oblenergo" / f"{hromada_id}_meta.json"
        meta = read_json(meta_path) if meta_path.exists() else {}
        curr_sha = meta.get("sha256", "")
        xlsx_path = resolve_path(meta["file_path"]) if meta.get("file_path") else Path("")

        report["source"] = {
            "url": meta.get("source_url", ""),
            "sha256": curr_sha,
            "last_modified": meta.get("last_modified", ""),
            "changed": bool(curr_sha and curr_sha != prev_sha),
        }

        # Early exit if source sha256 unchanged
        if curr_sha and curr_sha == prev_sha and not args.force_rebuild:
            report["status"] = "unchanged"
            report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            write_run_report(report)
            print(f"Source unchanged (sha256={curr_sha[:12]}...). Exiting with code 10.")
            sys.exit(10)

        # Guard 1: XLSX file signature & header
        xlsx_guards = check_xlsx_file(xlsx_path, profile.get("guards", {}))
        report["guards"].extend(xlsx_guards)
        if not all_guards_ok(xlsx_guards):
            failed = first_failed_guard(xlsx_guards)
            report["status"] = "guard_failed"
            report["steps"]["guards"] = f"failed:{failed}"
            report["error"] = f"Guard failed: {failed}"
            report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            write_run_report(report)
            print(f"Guard failed ({failed}). State and output untouched. Exiting with code 2.")
            sys.exit(2)

        # Previous records count for guard comparison
        set1_path = ROOT / "data" / "set1_oblenergo" / f"{hromada_id}.json"
        prev_records_count = read_json(set1_path).get("total_records") if set1_path.exists() else None

        # 2. Parse
        run_step("src/parse_oblenergo.py", str(profile_path))
        report["steps"]["parse"] = "ok"
        set1 = read_json(set1_path)

        # 3. OSM, Compare & optional Visicom
        run_step("src/fetch_osm.py", str(profile_path))
        run_step("src/compare_sets.py", str(profile_path))
        if not args.skip_fetch and (ROOT / "src" / "fetch_visicom.py").exists():
            run_step("src/fetch_visicom.py", str(profile_path))
            run_step("src/compare_sets.py", str(profile_path))

        # 4. Dry-run sync + quantitative guards BEFORE mutating state/output
        dry = run_sync(profile_path, run_id=run_id, dry_run=True)
        ds = dry["diff_summary"]
        matched_settlements = len({r["settlement"] for r in set1.get("records", [])})
        changed_addrs = (
            ds["counts"].get("subqueue_changed", 0)
            + ds["counts"].get("subqueue_added", 0)
            + ds["counts"].get("subqueue_removed", 0)
        )

        quant_guards = check_parsed_and_sync(
            profile=profile,
            matched_settlements=matched_settlements,
            records_count=set1.get("total_records", 0),
            prev_records_count=prev_records_count,
            changed_addresses=changed_addrs,
            total_active_addresses=ds["total_active"],
            review_count=len(dry["review"]),
            accept_mass_change=args.accept_mass_change,
        )
        report["guards"].extend(quant_guards)

        if not all_guards_ok(quant_guards):
            failed = first_failed_guard(quant_guards)
            report["status"] = "guard_failed"
            report["steps"]["guards"] = f"failed:{failed}"
            report["error"] = f"Guard failed: {failed}"
            report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            write_run_report(report)
            print(f"Guard failed ({failed}). State and output untouched. Exiting with code 2.")
            sys.exit(2)

        report["steps"]["guards"] = "ok"

        # 5. Commit sync
        sync_res = run_sync(profile_path, run_id=run_id, dry_run=False)
        report["steps"]["sync"] = "ok"

        # 6. Build output v2
        out_res = build_all_outputs(
            profile_path,
            excluded_items=sync_res["excluded"],
            review_items=sync_res["review"],
        )
        report["steps"]["build"] = "ok"

        report["status"] = "ok"
        report["counts"] = {
            "records": set1.get("total_records", 0),
            "settlements": f"{matched_settlements}/{len(profile.get('settlements', []))}",
            "addresses": out_res["counts"]["addresses"],
            "excluded": out_res["excluded_count"],
            "review": out_res["review_count"],
        }
        report["changes"] = {
            "added": sync_res["diff_summary"]["counts"].get("added", 0),
            "subqueue_changed": sync_res["diff_summary"]["counts"].get("subqueue_changed", 0),
            "gone": sync_res["diff_summary"]["counts"].get("gone", 0),
            "mass_change": sync_res["diff_summary"]["mass_change"],
        }
        report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        write_run_report(report)

        # Optional: generate HTML report if report.py exists (Stage 5)
        report_script = ROOT / "src" / "report.py"
        if report_script.exists():
            run_step("src/report.py", str(profile_path))

        print("\n=== Pipeline completed successfully ===")
    except SystemExit:
        raise
    except Exception as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        write_run_report(report)
        raise
    finally:
        release_lock(lock_path)


if __name__ == "__main__":
    main()
