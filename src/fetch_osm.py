import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io import ROOT, http_request_with_retry, read_json, write_json_atomic

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {
    "User-Agent": "AdresBazaPidcherg-SvitloSk/2.0",
    "Accept": "application/json",
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_hromada_osm(profile: dict[str, Any]) -> dict[str, Any]:
    """Query Overpass API by osm_relation for settlements (with katotth) and streets."""
    osm_rel = profile.get("osm_relation")
    if osm_rel:
        area_filter = f"rel({int(osm_rel)});map_to_area->.hromada;"
    else:
        short_name = profile["name"].replace(" територіальна", "")
        area_filter = f'area["name"="{short_name}"]["boundary"="administrative"]->.hromada;'

    query = f"""
    [out:json][timeout:90];
    {area_filter}
    (
      node["place"~"^(city|town|village|hamlet)$"]["name"](area.hromada);
      way["place"~"^(city|town|village|hamlet)$"]["name"](area.hromada);
      relation["place"~"^(city|town|village|hamlet)$"]["name"](area.hromada);
      relation["boundary"="administrative"]["katotth"](area.hromada);
      way["highway"]["name"](area.hromada);
    );
    out tags center;
    """

    resp = http_request_with_retry("POST", OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=120)
    return resp.json()


def build_osm_payload(profile: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    elements = raw.get("elements", [])

    # 1. Collect settlements with coordinates and katotth
    settlements_map: dict[str, dict[str, Any]] = {}
    for el in elements:
        tags = el.get("tags", {})
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        is_place = tags.get("place") in {"city", "town", "village", "hamlet"}
        is_admin = tags.get("boundary") == "administrative" and "katotth" in tags
        if not (is_place or is_admin):
            continue

        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        katotth = (tags.get("katotth") or "").strip() or None

        entry = settlements_map.setdefault(
            name,
            {
                "name": name,
                "place": tags.get("place"),
                "katotth": katotth,
                "lat": lat,
                "lon": lon,
                "streets": set(),
            },
        )
        if katotth and not entry["katotth"]:
            entry["katotth"] = katotth
        if lat is not None and lon is not None and (entry["lat"] is None or is_place):
            entry["lat"] = lat
            entry["lon"] = lon
        if tags.get("place") and not entry["place"]:
            entry["place"] = tags.get("place")

    # Prepare list of settlements with coordinates for nearest-settlement assignment
    geo_settlements = [
        (name, info["lat"], info["lon"])
        for name, info in settlements_map.items()
        if info["lat"] is not None and info["lon"] is not None
    ]

    # 2. Collect streets and assign to settlement (by addr:city or nearest settlement center)
    all_streets: set[str] = set()
    highway_count = 0
    for el in elements:
        tags = el.get("tags", {})
        if "highway" not in tags or not tags.get("name"):
            continue
        highway_count += 1
        st_name = tags["name"].strip()
        all_streets.add(st_name)

        assigned: str | None = None
        addr_city = (tags.get("addr:city") or "").strip()
        if addr_city and addr_city in settlements_map:
            assigned = addr_city
        else:
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lon = el.get("lon") or el.get("center", {}).get("lon")
            if lat is not None and lon is not None and geo_settlements:
                best_name = min(geo_settlements, key=lambda s: _haversine_km(lat, lon, s[1], s[2]))[0]
                assigned = best_name

        if assigned:
            settlements_map[assigned]["streets"].add(st_name)

    settlements_out = {
        name: {
            "place": info["place"],
            "katotth": info["katotth"],
            "lat": info["lat"],
            "lon": info["lon"],
            "streets": sorted(info["streets"]),
        }
        for name, info in sorted(settlements_map.items())
    }

    return {
        "hromada_id": profile["id"],
        "katottg": profile.get("katottg"),
        "osm_relation": profile.get("osm_relation"),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_elements_count": len(elements),
        "highway_ways_count": highway_count,
        "unique_streets": len(all_streets),
        "settlements_count": len(settlements_out),
        "streets": sorted(all_streets),
        "settlements": settlements_out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch settlements (katotth) and streets from OSM (Overpass API)")
    parser.add_argument("--profile", default="profiles/hromada.json", help="Path to hromada profile JSON")
    parser.add_argument("--force", action="store_true", help="Ignore local cache and re-fetch from Overpass API")
    args = parser.parse_args()

    profile = read_json(args.profile)
    hromada_id = profile["id"]

    out_dir = ROOT / "data" / "set2_external"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{hromada_id}_osm.json"

    if out_path.exists() and not args.force:
        cached = read_json(out_path)
        if "settlements" in cached:
            print(
                f"Using cached OSM data ({cached.get('settlements_count', 0)} settlements, "
                f"{cached.get('unique_streets', 0)} unique streets) -> {out_path}"
            )
            return

    print(
        f"[1/2] Querying Overpass API for '{profile['name']}' "
        f"(osm_relation={profile.get('osm_relation')}, katottg={profile.get('katottg')})..."
    )
    raw = fetch_hromada_osm(profile)
    payload = build_osm_payload(profile, raw)
    write_json_atomic(out_path, payload)
    print(
        f"[2/2] Saved {payload['settlements_count']} settlements and {payload['unique_streets']} unique streets "
        f"-> {out_path}"
    )


if __name__ == "__main__":
    main()
