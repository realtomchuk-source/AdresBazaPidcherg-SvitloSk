import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fetch_visicom import extract_features, pick_best_street_feature


def test_extract_features_collection():
    payload = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"name": "Вишнева"}}],
    }
    assert len(extract_features(payload)) == 1


def test_extract_features_single():
    payload = {"type": "Feature", "properties": {"name": "Вишнева"}}
    assert len(extract_features(payload)) == 1


def test_pick_best_street_feature_matches_settlement():
    features = [
        {
            "type": "Feature",
            "properties": {"settlement": "с. Інше", "type": "вул.", "name": "Вишнева"},
            "geo_centroid": {"coordinates": [27.0, 49.0]},
        },
        {
            "type": "Feature",
            "properties": {"settlement": "с. Іршики", "type": "вул.", "name": "Вишнева"},
            "geo_centroid": {"coordinates": [27.2, 49.8]},
        },
    ]
    best = pick_best_street_feature(features, "Іршики")
    assert best is not None
    assert best["name"] == "вул. Вишнева"
    assert best["settlement"] == "с. Іршики"
    assert best["lat"] == 49.8
