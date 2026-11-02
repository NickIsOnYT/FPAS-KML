import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import app


class AlertRegressionTests(unittest.TestCase):
    def test_negative_utc_offset_is_converted_before_sorting(self):
        alert = {"raw_effective": "2026-01-01T10:00:00-05:00"}

        self.assertEqual(
            app.get_alert_start_time(alert).isoformat(),
            "2026-01-01T15:00:00+00:00",
        )

    def test_dateline_longitudes_produce_a_dateline_centroid(self):
        geometries = [{"coords": [(179.0, 10.0), (-179.0, 10.0)]}]

        longitude, latitude = app.calculate_centroid_from_geometries(geometries)

        self.assertAlmostEqual(abs(longitude), 180.0)
        self.assertAlmostEqual(latitude, 10.0)

    def test_map_feed_centers_alerts_across_the_dateline_correctly(self):
        alert = {
            "id": "dateline",
            "event_type": "Test alert",
            "severity": "Minor",
            "geometries": [{
                "type": "polygon",
                "coords": [(179.0, 10.0), (-179.0, 10.0)],
                "location_name": "Dateline",
            }],
        }

        with (
            patch.dict(app.ALERT_CACHE, {"dateline": alert}, clear=True),
            patch.object(app, "NEW_ALERT_IDS", set()),
            patch.object(app, "load_hidden_alert_ids", return_value=set()),
            patch.object(app, "load_custom_alerts", return_value=[]),
        ):
            result = app.get_web_alerts()

        self.assertAlmostEqual(abs(result[0]["center"][1]), 180.0)
        self.assertAlmostEqual(result[0]["center"][0], 10.0)

    def test_new_and_existing_alerts_at_same_location_keep_separate_folders(self):
        coordinates = [(10.0, 20.0), (10.1, 20.0), (10.0, 20.1)]

        def make_alert(alert_id):
            return {
                "id": alert_id,
                "event_type": "flood warning",
                "severity": "Severe",
                "raw_effective": "2026-01-01T00:00:00Z",
                "effective": "2026-01-01 00:00",
                "expires": "2026-01-02 00:00",
                "geometries": [{
                    "type": "polygon",
                    "language": "en-CA",
                    "event_title": "flood warning",
                    "location_name": "Test Region",
                    "description": "Test description",
                    "instruction": "",
                    "links": [],
                    "coords": coordinates,
                }],
            }

        with (
            patch.dict(
                app.ALERT_CACHE,
                {"existing": make_alert("existing"), "new": make_alert("new")},
                clear=True,
            ),
            patch.object(app, "NEW_ALERT_IDS", {"new"}),
            patch.object(app, "load_hidden_alert_ids", return_value=set()),
            patch.object(app, "load_custom_alerts", return_value=[]),
        ):
            response = app.app.test_client().get("/alerts.kml")

        self.assertEqual(response.status_code, 200)
        root = ET.fromstring(response.data)
        namespace = {"kml": "http://www.opengis.net/kml/2.2"}
        parents = {
            child: parent
            for parent in root.iter()
            for child in parent
        }

        def ancestor_folder_names(element):
            names = []
            current = parents.get(element)
            while current is not None:
                if current.tag.endswith("Folder"):
                    name = current.find("kml:name", namespace)
                    if name is not None:
                        names.append(name.text)
                current = parents.get(current)
            return names

        pin_paths = {}
        for placemark in root.findall(".//kml:Placemark", namespace):
            if placemark.find("kml:Point", namespace) is None:
                continue
            description = placemark.findtext("kml:description", default="", namespaces=namespace)
            alert_id = "new" if "new" in description else "existing"
            pin_paths[alert_id] = ancestor_folder_names(placemark)

        self.assertIn("New Alerts", pin_paths["new"])
        self.assertIn("Storms, Tornados, Floods & Winds", pin_paths["existing"])
        self.assertNotIn("New Alerts", pin_paths["existing"])


if __name__ == "__main__":
    unittest.main()
