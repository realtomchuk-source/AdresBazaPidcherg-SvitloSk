import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io import ROOT, http_request_with_retry, read_json, write_json_atomic

GPV_URL = "https://hoe.com.ua/page/pogodinni-vidkljuchennja"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def load_profile(profile_path: str | Path) -> dict:
    return read_json(profile_path)


def find_rem_file_url(html: str, base_url: str, keywords: list[str]) -> tuple[str, str] | None:
    """Find the best matching XLSX (preferred) or PDF link for the given REM keywords."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[tuple[int, str, str]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = " ".join(a.get_text(" ", strip=True).split())
        combined = f"{text} {href}".lower()

        if not any(kw.lower() in combined for kw in keywords):
            continue

        lower_href = href.lower()
        if ".xlsx" in lower_href or ".xls" in lower_href:
            candidates.append((1, urljoin(base_url, href), text))
        elif ".pdf" in lower_href:
            candidates.append((2, urljoin(base_url, href), text))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    _, best_url, best_text = candidates[0]
    return best_url, best_text


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch REM schedule file from hoe.com.ua")
    parser.add_argument(
        "--profile",
        default="profiles/hromada.json",
        help="Path to hromada profile JSON",
    )
    args = parser.parse_args()

    profile_path = Path(args.profile)
    profile = load_profile(profile_path)
    hromada_id = profile["id"]
    keywords = profile["oblenergo"]["file_keywords"]
    page_url = profile["oblenergo"].get("page_url", GPV_URL)

    print(f"[1/3] Fetching {page_url} for '{profile['oblenergo']['rem_name']}'...")
    resp = http_request_with_retry("GET", page_url, headers=HEADERS, timeout=30)

    found = find_rem_file_url(resp.text, page_url, keywords)
    if not found:
        raise RuntimeError(f"Could not find XLSX/PDF link matching keywords: {keywords}")

    file_url, link_text = found
    print(f"[2/3] Found link: '{link_text}' -> {file_url}")

    file_resp = http_request_with_retry("GET", file_url, headers=HEADERS, timeout=60)
    content = file_resp.content
    if not content:
        raise RuntimeError("Downloaded file is empty.")

    file_hash = sha256_bytes(content)
    raw_dir = ROOT / "data" / "raw_oblenergo"
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta_path = raw_dir / f"{hromada_id}_meta.json"

    if meta_path.exists():
        prev_meta = read_json(meta_path)
        if prev_meta.get("sha256") == file_hash:
            print(f"[3/3] No changes detected (sha256={file_hash[:12]}...). Skipping save.")
            return

    ext = ".xlsx" if ".xlsx" in file_url.lower() else ".pdf"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = raw_dir / f"{hromada_id}_{stamp}{ext}"
    out_file.write_bytes(content)

    meta = {
        "hromada_id": hromada_id,
        "rem_name": profile["oblenergo"]["rem_name"],
        "source_url": file_url,
        "link_text": link_text,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "last_modified": file_resp.headers.get("Last-Modified", ""),
        "sha256": file_hash,
        "file_path": str(out_file.relative_to(ROOT)),
        "size_bytes": len(content),
    }
    write_json_atomic(meta_path, meta)
    print(f"[3/3] Saved {out_file} ({len(content)} bytes, sha256={file_hash[:12]}...)")


if __name__ == "__main__":
    main()
