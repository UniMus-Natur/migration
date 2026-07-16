"""Unit tests for MUSIT → Specify coordinate mapping (no database)."""

from __future__ import annotations

import json
import unittest

from flows.lib.musit_coordinate_map import (
    MAPPING_VERSION,
    apply_verbatim_coordinate_fields,
    build_coordinate_remarks_payload,
    build_utm_geojson,
    format_locality_remarks_json,
    locality_spatial_kwargs_from_musit_koordinate,
    normalize_musit_datum,
    verbatim_coordinate_string,
)


class MusitCoordinateMapTests(unittest.TestCase):
    def test_verbatim_mgrs_goes_to_text3(self) -> None:
        out: dict = {}
        apply_verbatim_coordinate_fields(out, "NM 71,56")
        self.assertEqual(out["text3"], "NM 71,56")
        self.assertNotIn("lat1text", out)

    def test_primary_decimal_prefers_derived_over_kp(self) -> None:
        coord = {
            "latitude_l": 59.27,
            "longitude_l": 8.59,
            "dc_latitude": 58.3511,
            "dc_longitude": 8.6506,
            "coordinate_string": "MK 793-797,676-680",
            "datum": "WGS84",
            "koordinate_place_id": 1,
            "coordinate_type_term": "MGRS",
        }
        out = locality_spatial_kwargs_from_musit_koordinate(
            coord, owner="MUSIT_BOTANIKK_FELLES", place_id=99
        )
        self.assertEqual(out["latitude1"], 58.3511)
        self.assertEqual(out["longitude1"], 8.6506)
        self.assertEqual(out["lat1text"], "58.3511")
        self.assertEqual(out["long1text"], "8.6506")
        self.assertEqual(out["latlongtype"], "Point")
        self.assertEqual(out["text3"], "MK 793-797,676-680")
        self.assertNotIn("latitude2", out)
        remarks = json.loads(out["remarks"])
        self.assertEqual(remarks["migration_meta"]["kind"], "musit-coordinate-notes")
        self.assertEqual(remarks["notes"]["coord_conflict"]["kp"], [59.27, 8.59])
        audit = json.loads(out["text4"])
        self.assertEqual(audit["stored"]["primary_source"], "dc")
        self.assertIsNotNone(audit["stored"]["conflict"])
        self.assertEqual(audit["migration_meta"]["mapping_version"], MAPPING_VERSION)

    def test_kp_decimals_used_when_derived_missing(self) -> None:
        coord = {
            "latitude_l": 61.0865,
            "longitude_l": 9.7030,
            "dc_latitude": None,
            "dc_longitude": None,
            "coordinate_string": "NN 35,50",
            "datum": "ED50",
            "koordinate_place_id": 2,
            "coordinate_type_term": "MGRS",
        }
        out = locality_spatial_kwargs_from_musit_koordinate(coord)
        self.assertEqual(out["latitude1"], 61.0865)
        self.assertEqual(out["longitude1"], 9.7030)
        self.assertEqual(out["text3"], "NN 35,50")
        audit = json.loads(out["text4"])
        self.assertEqual(audit["stored"]["primary_source"], "kp")

    def test_no_dms_parsing_from_string(self) -> None:
        coord = {
            "latitude_l": None,
            "longitude_l": None,
            "dc_latitude": None,
            "dc_longitude": None,
            "coordinate_string": "59°48.185'N 10°44.478'E",
            "koordinate_place_id": 3,
        }
        out = locality_spatial_kwargs_from_musit_koordinate(coord)
        self.assertNotIn("latitude1", out)
        self.assertNotIn("longitude1", out)
        self.assertNotIn("text3", out)
        remarks = json.loads(out["remarks"])
        self.assertEqual(remarks["notes"]["verbatim_coordinate"], "59°48.185'N 10°44.478'E")

    def test_mgrs_l_fallback_for_verbatim(self) -> None:
        coord = {"coordinate_string": None, "mgrs_l": "KN 80,05"}
        self.assertEqual(verbatim_coordinate_string(coord), "KN 80,05")

    def test_normalize_datum_maps_wgs84_and_rejects_zone_number(self) -> None:
        self.assertEqual(normalize_musit_datum("WGS 84"), ("WGS84", "WGS 84"))
        self.assertEqual(normalize_musit_datum("32"), (None, "32"))

    def test_musit_precision_maps_to_latlongaccuracy(self) -> None:
        coord = {
            "latitude_l": 59.0,
            "longitude_l": 10.0,
            "precision": 100,
            "accuracy": 101,
            "coordinate_string": "NL 9435,9010",
            "datum": "WGS84",
            "koordinate_place_id": 4,
            "coordinate_type_term": "MGRS",
        }
        out = locality_spatial_kwargs_from_musit_koordinate(coord)
        self.assertEqual(out["latlongaccuracy"], 100.0)
        self.assertNotIn("MUSIT PRECISION", out.get("remarks") or "")
        audit = json.loads(out["text4"])
        self.assertEqual(audit["uncertainty"]["musit_precision_m"], 100)
        self.assertEqual(audit["migration_meta"]["mapping_version"], MAPPING_VERSION)

    def test_mgrs_with_derived_utm_and_wgs(self) -> None:
        """Regression for O-V-2002713: WGS decimals + UTM GeoJSON + MGRS text3."""
        coord = {
            "coordinate_string": "CS 163,372",
            "mgrs_l": "CS 163,372",
            "latitude_l": None,
            "longitude_l": None,
            "dc_latitude": 28.3486,
            "dc_longitude": -16.8737,
            "dc_utm_x": 316350,
            "dc_utm_y": 3137250,
            "zone": 28,
            "belt": "R",
            "datum": "WGS84",
            "dc_datum": "WGS84",
            "precision": 2236.068,
            "coordinate_type_term": "MGRS",
            "utm_senere": "1",
            "koordinate_place_id": 201901,
        }
        out = locality_spatial_kwargs_from_musit_koordinate(
            coord, owner="MUSIT_BOTANIKK_FELLES", place_id=461702
        )
        self.assertEqual(out["latitude1"], 28.3486)
        self.assertEqual(out["longitude1"], -16.8737)
        self.assertEqual(out["lat1text"], "28.3486")
        self.assertEqual(out["long1text"], "-16.8737")
        self.assertEqual(out["latlongtype"], "Point")
        self.assertEqual(out["text3"], "CS 163,372")
        self.assertNotIn("lat2text", out)
        self.assertNotIn("latitude2", out)
        self.assertIs(out["yesno2"], True)
        geo = json.loads(out["text5"])
        self.assertEqual(geo["type"], "Feature")
        self.assertEqual(geo["geometry"]["coordinates"], [316350, 3137250])
        self.assertEqual(geo["properties"]["zone"], 28)
        self.assertEqual(geo["properties"]["crs"], "EPSG:32628")
        self.assertEqual(geo["properties"]["source"], "dc")

    def test_utm_geojson_includes_high_corner(self) -> None:
        geo = build_utm_geojson(
            {
                "utm_x": 479519,
                "utm_y": 6569492,
                "utm_x_h": 479619,
                "utm_y_h": 6569592,
                "zone": 32,
                "belt": "V",
            }
        )
        assert geo is not None
        self.assertEqual(geo["geometry"]["coordinates"], [479519, 6569492])
        self.assertEqual(geo["properties"]["high"], [479619, 6569592])

    def test_does_not_map_ll_high_to_lat2(self) -> None:
        coord = {
            "latitude_l": 17.117,
            "longitude_l": -25.033,
            "latitude_h": 17.133,
            "longitude_h": None,
            "coordinate_string": "17°7'-17°8'N 25°2'-25°4'W",
            "coordinate_type_term": "UNKNOWN",
            "koordinate_place_id": 9,
        }
        out = locality_spatial_kwargs_from_musit_koordinate(coord)
        self.assertEqual(out["latitude1"], 17.117)
        self.assertEqual(out["longitude1"], -25.033)
        self.assertNotIn("latitude2", out)
        self.assertNotIn("longitude2", out)
        self.assertEqual(out["latlongtype"], "Point")

    def test_remarks_omitted_when_nothing_to_note(self) -> None:
        coord = {
            "latitude_l": 59.0,
            "longitude_l": 10.0,
            "coordinate_string": "NL 9435,9010",
            "datum": "WGS84",
            "koordinate_place_id": 7,
            "coordinate_type_term": "MGRS",
        }
        out = locality_spatial_kwargs_from_musit_koordinate(coord)
        self.assertNotIn("remarks", out)

    def test_format_locality_remarks_json_is_valid(self) -> None:
        payload = build_coordinate_remarks_payload(
            {"map_sheet": "1234", "zone": 32, "belt": "V"},
            kp_datum_unmapped="32",
        )
        assert payload is not None
        text = format_locality_remarks_json(payload)
        parsed = json.loads(text)
        self.assertIn("map_sheet", parsed["notes"])
        self.assertIn("utm_zone", parsed["notes"])

    def test_ca_utm_and_coord_added_later_map_to_yesno(self) -> None:
        coord = {
            "latitude_l": 59.0,
            "longitude_l": 10.0,
            "coordinate_string": "NL 9435,9010",
            "datum": "WGS84",
            "ca_utm": "1",
            "utm_senere": "0",
            "koordinate_place_id": 5,
            "coordinate_type_term": "MGRS",
        }
        out = locality_spatial_kwargs_from_musit_koordinate(coord)
        self.assertIs(out["yesno1"], True)
        self.assertIs(out["yesno2"], False)
        self.assertNotIn("remarks", out)
        audit = json.loads(out["text4"])
        self.assertIs(audit["flags"]["ca_utm"], True)
        self.assertIs(audit["flags"]["utm_senere"], False)


if __name__ == "__main__":
    unittest.main()
