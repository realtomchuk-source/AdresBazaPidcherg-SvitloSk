import argparse
import html
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io import ROOT, read_json

ISSUE_LABELS = {
    "other_token": "Нестандартний номер",
    "suspicious_range": "Сумнівний діапазон",
    "osm_fuzzy": "Схожа назва вулиці",
    "ambiguous_village_street": "Підозріла вулиця (однойменне село)",
    "multi_subqueue_village": "Дві підчерги в селі",
    "override_conflict": "Конфлікт ручної правки",
    "invalid_subqueue": "Некоректна підчерга",
    "empty_houses": "Порожній список будинків",
    "unmatched_street": "Немає на карті OSM/Visicom",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def generate_html_report(profile_path: str | Path) -> Path:
    profile = read_json(profile_path)
    out_dir = ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    run_report = read_json(out_dir / "run_report.json") if (out_dir / "run_report.json").exists() else {}
    addresses = read_json(out_dir / "addresses.json") if (out_dir / "addresses.json").exists() else {}
    needs_review = read_json(out_dir / "needs_review.json") if (out_dir / "needs_review.json").exists() else []
    excluded_log = read_json(out_dir / "excluded_log.json") if (out_dir / "excluded_log.json").exists() else []

    runs = _read_jsonl(ROOT / "data" / "reports" / "runs.jsonl")[-10:]
    runs.reverse()

    history = _read_jsonl(ROOT / "data" / "state" / "history.jsonl")
    now = datetime.now(timezone.utc)
    cut_7 = (now - timedelta(days=7)).isoformat()
    cut_30 = (now - timedelta(days=30)).isoformat()

    changes_7 = [e for e in history if e.get("ts", "") >= cut_7 and e.get("type") in {"subqueue_changed", "subqueue_added", "subqueue_removed", "mass_change"}]
    changes_30 = [e for e in history if e.get("ts", "") >= cut_30 and e.get("type") in {"subqueue_changed", "subqueue_added", "subqueue_removed", "mass_change"}]

    actionable_review = [r for r in needs_review if r.get("issue") != "unmatched_street"]
    unmatched_review = [r for r in needs_review if r.get("issue") == "unmatched_street"]

    meta = addresses.get("meta", {})
    counts = meta.get("counts", {})

    embedded_data = {
        "hromada_name": profile.get("name", ""),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "meta": meta,
        "counts": counts,
        "excluded_count": len(excluded_log),
        "actionable_count": len(actionable_review),
        "unmatched_count": len(unmatched_review),
        "latest_run": run_report,
        "runs": runs,
        "changes_7_count": len(changes_7),
        "changes_30_count": len(changes_30),
        "mass_changes_30": [e for e in changes_30 if e.get("type") == "mass_change"],
        "recent_changes": [e for e in changes_30 if e.get("type") != "mass_change"][-150:][::-1],
        "actionable_review": actionable_review,
        "unmatched_review": unmatched_review,
        "issue_labels": ISSUE_LABELS,
    }

    data_json = json.dumps(embedded_data, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"SvitloSk — {profile.get('name', '')}")

    html_doc = f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --muted: #94a3b8; --ok: #22c55e; --warn: #f59e0b; --err: #ef4444; --accent: #38bdf8; --border: #334155; }}
  body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 24px; line-height: 1.45; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 18px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }}
  .kpi {{ font-size: 24px; font-weight: 700; }}
  .kpi-label {{ font-size: 12px; color: var(--muted); }}
  .tabs {{ display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 10px; flex-wrap: wrap; }}
  .tab-btn {{ background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px 16px; font-size: 14px; font-weight: 600; cursor: pointer; }}
  .tab-btn.active {{ background: var(--accent); color: #0f172a; border-color: var(--accent); }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
  .banner {{ background: rgba(245,158,11,.12); border: 1px solid rgba(245,158,11,.4); color: #fde68a; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; font-size: 13px; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
  th {{ background: #0b1220; color: var(--muted); font-weight: 600; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
  .b-ok {{ background: rgba(34,197,94,.2); color: var(--ok); }}
  .b-unchanged {{ background: rgba(148,163,184,.2); color: var(--muted); }}
  .b-warn {{ background: rgba(245,158,11,.2); color: var(--warn); }}
  .b-err {{ background: rgba(239,68,68,.2); color: var(--err); }}
  .toolbar {{ display: flex; gap: 10px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }}
  select, input {{ background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 13px; }}
  button.copy {{ background: var(--accent); color: #0f172a; border: none; border-radius: 6px; padding: 4px 10px; font-size: 12px; font-weight: 600; cursor: pointer; }}
  details {{ margin-top: 18px; background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; }}
  summary {{ cursor: pointer; font-weight: 600; color: var(--muted); }}
  code {{ background: #0b1220; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
</style>
</head>
<body>
  <h1 id="title">SvitloSk</h1>
  <div class="sub" id="subtitle"></div>

  <div class="grid" id="kpis"></div>

  <div class="tabs">
    <button class="tab-btn active" onclick="openTab('changes', this)">Зміни підчерг</button>
    <button class="tab-btn" onclick="openTab('review', this)" id="tab-review-btn">Потребує рішення</button>
    <button class="tab-btn" onclick="openTab('runs', this)">Статус запусків</button>
  </div>

  <div id="tab-changes" class="tab-panel active">
    <div id="mass-banner"></div>
    <div class="toolbar">
      <input id="chg-search" type="text" placeholder="Пошук по селу або вулиці...">
    </div>
    <table>
      <thead><tr><th>Дата</th><th>Населений пункт / Адреса</th><th>Було</th><th>Стало</th></tr></thead>
      <tbody id="changes-body"></tbody>
    </table>
  </div>

  <div id="tab-review" class="tab-panel">
    <div class="toolbar">
      <label>Категорія: <select id="issue-filter"><option value="">Усі</option></select></label>
      <input id="rev-search" type="text" placeholder="Пошук по селу / вулиці...">
    </div>
    <table>
      <thead><tr><th>Що трапилось</th><th>Населений пункт</th><th>Вулиця</th><th>Номер</th><th>Пояснення</th><th>Дія</th></tr></thead>
      <tbody id="review-body"></tbody>
    </table>

    <details>
      <summary id="unmatched-summary">Довідково: вулиці без мітки на OSM/Visicom (уже є в базі)</summary>
      <table style="margin-top: 10px;">
        <thead><tr><th>Населений пункт</th><th>Вулиця</th></tr></thead>
        <tbody id="unmatched-body"></tbody>
      </table>
    </details>
  </div>

  <div id="tab-runs" class="tab-panel">
    <table>
      <thead><tr><th>Запуск</th><th>Статус</th><th>Джерело</th><th>Last-Modified</th><th>Адрес у базі</th><th>Змін підчерг</th><th>Помилка</th></tr></thead>
      <tbody id="runs-body"></tbody>
    </table>
  </div>

<script>
const DATA = {data_json};

function esc(s) {{
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function badge(status) {{
  const map = {{ ok: ['Оновлено', 'b-ok'], unchanged: ['Без змін', 'b-unchanged'], guard_failed: ['Зупинено захистом', 'b-warn'], error: ['Помилка', 'b-err'] }};
  const [label, cls] = map[status] || [status, 'b-err'];
  return `<span class="badge ${{cls}}">${{esc(label)}}</span>`;
}}

function openTab(id, btn) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  btn.classList.add('active');
}}

document.getElementById('title').textContent = `SvitloSk — ${{DATA.hromada_name}}`;
document.getElementById('subtitle').textContent = `Версія бази: ${{DATA.meta.version || '—'}} | Оновлено: ${{DATA.generated_at.slice(0,16).replace('T',' ')}} UTC`;
document.getElementById('tab-review-btn').textContent = `Потребує рішення (${{DATA.actionable_count}})`;
document.getElementById('unmatched-summary').textContent = `Довідково: вулиці без мітки на OSM/Visicom (${{DATA.unmatched_count}}) — вони вже є в чистому addresses.json`;

const kpis = [
  ['Населені пункти', DATA.counts.settlements ?? '—'],
  ['Вулиці', DATA.counts.streets ?? '—'],
  ['Активні адреси', DATA.counts.addresses ?? '—'],
  ['Зміни (7 / 30 дн.)', `${{DATA.changes_7_count}} / ${{DATA.changes_30_count}}`],
  ['Потребує рішення', DATA.actionable_count],
  ['Виключено', DATA.excluded_count],
];
document.getElementById('kpis').innerHTML = kpis.map(([l, v]) =>
  `<div class="card"><div class="kpi">${{esc(v)}}</div><div class="kpi-label">${{esc(l)}}</div></div>`
).join('');

// Tab 1: Changes
if (DATA.mass_changes_30.length) {{
  const txt = DATA.mass_changes_30.map(m => `<b>${{esc(m.settlement)}}</b> (${{m.changed}}/${{m.total}}: ${{(m.old||[]).join(',')}} → ${{(m.new||[]).join(',')}})`).join(' · ');
  document.getElementById('mass-banner').innerHTML = `<div class="banner">Масові зміни підчерг за останні 30 днів: ${{txt}}</div>`;
}}

function renderChanges() {{
  const q = document.getElementById('chg-search').value.trim().toLowerCase();
  const rows = DATA.recent_changes.filter(e => !q || (e.addr_id || e.settlement || '').toLowerCase().includes(q));
  document.getElementById('changes-body').innerHTML = rows.map(e => `
    <tr>
      <td>${{esc((e.ts || '').slice(0, 16).replace('T', ' '))}}</td>
      <td>${{esc((e.addr_id || e.settlement || '').replace(/\\|/g, ', '))}}</td>
      <td>${{esc((e.old || []).join(', '))}}</td>
      <td><b>${{esc((e.new || []).join(', '))}}</b></td>
    </tr>
  `).join('') || '<tr><td colspan="4">Змін підчерг за останні 30 днів немає</td></tr>';
}}
document.getElementById('chg-search').addEventListener('input', renderChanges);
renderChanges();

// Tab 2: Actionable Review
const issues = [...new Set(DATA.actionable_review.map(r => r.issue))].sort();
const sel = document.getElementById('issue-filter');
issues.forEach(i => {{ const o = document.createElement('option'); o.value = i; o.textContent = DATA.issue_labels[i] || i; sel.appendChild(o); }});

function copyText(txt, btn) {{
  navigator.clipboard?.writeText(txt);
  const old = btn.textContent;
  btn.textContent = 'Скопійовано';
  setTimeout(() => btn.textContent = old, 1200);
}}

function renderReview() {{
  const f = sel.value;
  const q = document.getElementById('rev-search').value.trim().toLowerCase();
  const rows = DATA.actionable_review.filter(r =>
    (!f || r.issue === f) &&
    (!q || `${{r.settlement}} ${{r.street}} ${{r.token}}`.toLowerCase().includes(q))
  );
  document.getElementById('review-body').innerHTML = rows.map(r => {{
    const label = DATA.issue_labels[r.issue] || r.issue;
    const tpl = r.fix_template ? JSON.stringify(r.fix_template) : '';
    const act = tpl ? `<button class="copy" onclick='copyText(${{JSON.stringify(tpl)}}, this)'>Копіювати</button>` : '—';
    return `<tr>
      <td>${{esc(label)}}</td>
      <td>${{esc(r.settlement)}}</td>
      <td>${{esc(r.street)}}</td>
      <td>${{esc(r.token || '—')}}</td>
      <td>${{esc(r.suggestion || '—')}}</td>
      <td>${{act}}</td>
    </tr>`;
  }}).join('') || '<tr><td colspan="6">Усі проблеми вирішено!</td></tr>';
}}
sel.addEventListener('change', renderReview);
document.getElementById('rev-search').addEventListener('input', renderReview);
renderReview();

document.getElementById('unmatched-body').innerHTML = DATA.unmatched_review.map(r =>
  `<tr><td>${{esc(r.settlement)}}</td><td>${{esc(r.street)}}</td></tr>`
).join('');

// Tab 3: Runs
document.getElementById('runs-body').innerHTML = DATA.runs.map(r => `
  <tr>
    <td><code>${{esc(r.run_id)}}</code></td>
    <td>${{badge(r.status)}}</td>
    <td>${{r.source?.changed ? 'оновлено' : 'без змін'}}</td>
    <td>${{esc(r.source?.last_modified || '—')}}</td>
    <td>${{esc(r.counts?.addresses ?? '—')}}</td>
    <td>${{esc(r.changes?.subqueue_changed ?? 0)}}</td>
    <td>${{esc(r.error || '—')}}</td>
  </tr>
`).join('') || '<tr><td colspan="7">Запусків ще немає</td></tr>';
</script>
</body>
</html>
"""

    out_path = out_dir / "admin_report.html"
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate standalone HTML admin report")
    parser.add_argument("--profile", default="profiles/hromada.json")
    args = parser.parse_args()

    out_path = generate_html_report(args.profile)
    print(f"HTML admin report generated -> {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
