import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sync_addresses import check_ambiguous_village_streets, compute_sync


def _make_inc(settlement: str, street: str, house: str, subqueues: list[str], okrug: str = "Тест"):
    addr_id = f"{settlement}|{street}|{house}"
    return addr_id, {
        "settlement": settlement,
        "okrug": okrug,
        "street": street,
        "house": house,
        "subqueues": set(subqueues),
        "raw": {house},
    }


def test_added_and_subqueue_changed_and_mass_change():
    # Initial state: 2 houses in Гнатки with 3.1
    k1, v1 = _make_inc("Гнатки", "вул. Травнева", "1", ["3.1"])
    k2, v2 = _make_inc("Гнатки", "вул. Травнева", "2", ["3.1"])
    state1, _, _, _ = compute_sync(
        {"schema": 1, "items": {}},
        {k1: v1, k2: v2},
        source_sha256="sha1",
        run_id="r1",
        now_iso="2026-11-23T07:30:00Z",
        overrides={},
    )

    # Next run: both changed 3.1 -> 2.1, plus new house 3
    k1b, v1b = _make_inc("Гнатки", "вул. Травнева", "1", ["2.1"])
    k2b, v2b = _make_inc("Гнатки", "вул. Травнева", "2", ["2.1"])
    k3, v3 = _make_inc("Гнатки", "вул. Травнева", "3", ["2.1"])
    state2, events, _, ds = compute_sync(
        state1,
        {k1b: v1b, k2b: v2b, k3: v3},
        source_sha256="sha2",
        run_id="r2",
        now_iso="2026-11-25T07:30:00Z",
        overrides={},
    )

    types = [e["type"] for e in events]
    assert types.count("subqueue_changed") == 2
    assert types.count("added") == 1
    assert "mass_change" in types
    assert "Гнатки" in ds["mass_change"]
    assert state2["items"][k1]["subqueues"] == ["2.1"]


def test_subqueue_added():
    k1, v1 = _make_inc("Місто", "вул. Миру", "1", ["2.1"])
    s1, _, _, _ = compute_sync({"schema": 1, "items": {}}, {k1: v1}, source_sha256="s1", run_id="r1", now_iso="2026-11-23T00:00:00Z", overrides={})

    k1b, v1b = _make_inc("Місто", "вул. Миру", "1", ["2.1", "6.2"])
    _, events, _, _ = compute_sync(s1, {k1b: v1b}, source_sha256="s2", run_id="r2", now_iso="2026-11-24T00:00:00Z", overrides={})
    assert "subqueue_added" in [e["type"] for e in events]


def test_missing_gone_reappeared():
    k1, v1 = _make_inc("Місто", "вул. Миру", "1", ["2.1"])
    s1, _, _, _ = compute_sync({"schema": 1, "items": {}}, {k1: v1}, source_sha256="s1", run_id="r1", now_iso="2026-11-23T00:00:00Z", overrides={})

    # Missing 1st run -> stays active
    s2, ev2, _, _ = compute_sync(s1, {}, source_sha256="s2", run_id="r2", now_iso="2026-11-24T00:00:00Z", overrides={})
    assert [e["type"] for e in ev2] == ["missing"]
    assert s2["items"][k1]["status"] == "active"

    # Missing 2nd run -> gone
    s3, ev3, _, _ = compute_sync(s2, {}, source_sha256="s3", run_id="r3", now_iso="2026-11-25T00:00:00Z", overrides={})
    assert [e["type"] for e in ev3] == ["gone"]
    assert s3["items"][k1]["status"] == "gone"

    # Reappears -> active
    s4, ev4, _, _ = compute_sync(s3, {k1: v1}, source_sha256="s4", run_id="r4", now_iso="2026-11-26T00:00:00Z", overrides={})
    assert [e["type"] for e in ev4] == ["reappeared"]
    assert s4["items"][k1]["status"] == "active"


def test_override_conflict():
    k1, v1 = _make_inc("Місто", "вул. Миру", "1", ["5.2"])
    ov = {k1: {"addr_id": k1, "subqueues": ["2.1"], "reason": "checked"}}
    s1, events, reviews, _ = compute_sync({"schema": 1, "items": {}}, {k1: v1}, source_sha256="s1", run_id="r1", now_iso="2026-11-25T00:00:00Z", overrides=ov)
    assert s1["items"][k1]["subqueues"] == ["2.1"]
    assert any(e["type"] == "override_conflict" for e in events)
    assert len(reviews) == 1


def test_manual_exclude_is_immediate():
    k1, v1 = _make_inc("Решнівка", "вул. Садова", "1", ["5.2"])
    s1, _, _, _ = compute_sync({"schema": 1, "items": {}}, {k1: v1}, source_sha256="s1", run_id="r1", now_iso="2026-11-25T00:00:00Z", overrides={})

    s2, ev2, _, _ = compute_sync(
        s1,
        {},
        source_sha256="s2",
        run_id="r2",
        now_iso="2026-11-26T00:00:00Z",
        overrides={},
        excluded_streets={"Решнівка|вул. Садова"},
    )
    assert s2["items"][k1]["status"] == "excluded"
    assert [e["type"] for e in ev2] == ["excluded"]


def test_ambiguous_village_street_flagged():
    k1, v1 = _make_inc("Решнівка", "вул. Невідома", "1", ["5.2"])
    flagged = check_ambiguous_village_streets(
        {k1: v1},
        ambiguous_settlements={"Решнівка"},
        known_streets_by_settlement={"Решнівка": {"вишнева", "зарічна"}},
    )
    assert len(flagged) == 1
    assert flagged[0]["issue"] == "ambiguous_village_street"
