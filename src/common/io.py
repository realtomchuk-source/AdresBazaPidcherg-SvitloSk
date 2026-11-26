import json
import os
import time
from pathlib import Path
from typing import Any

import requests

ROOT: Path = Path(__file__).resolve().parents[2]


def resolve_path(path: str | Path) -> Path:
    """Resolve a path relative to project ROOT if it is not absolute."""
    p = Path(path)
    return p if p.is_absolute() else (ROOT / p)


def load_env_key(name: str) -> str | None:
    """Read a secret/config key from environment variables or local ROOT/.env file."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == name:
            cleaned = v.strip().strip('"').strip("'")
            return cleaned or None
    return None


def read_json(path: str | Path) -> Any:
    """Read JSON file using utf-8-sig to safely handle optional Windows BOM."""
    p = resolve_path(path)
    return json.loads(p.read_text(encoding="utf-8-sig"))


def write_json_atomic(
    path: str | Path,
    data: Any,
    *,
    indent: int | None = 2,
    compact: bool = False,
) -> Path:
    """Write JSON atomically via a temporary file and os.replace (UTF-8 without BOM)."""
    p = resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = p.with_suffix(p.suffix + ".tmp")

    if compact:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps(data, ensure_ascii=False, indent=indent)

    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, p)
    return p


def append_jsonl(path: str | Path, record: dict[str, Any]) -> Path:
    """Append a single JSON object as one line to a .jsonl file (UTF-8 without BOM)."""
    p = resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return p


def http_request_with_retry(
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    **kwargs: Any,
) -> requests.Response:
    """Execute an HTTP request with retry and exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_attempts:
                delay = backoff_base ** (attempt - 1)
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc
