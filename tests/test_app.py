from datetime import datetime, timezone
from io import BytesIO
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import app


class AlertRegressionTests(unittest.TestCase):
    CAP_XML = b"""<?xml version="1.0"?>
    <alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
        <identifier>xml-import-test</identifier>
        <sent>2026-01-01T00:00:00Z</sent>
        <status>Actual</status>
        <msgType>Alert</msgType>
        <info>
            <event>Imported test alert</event>
            <severity>Severe</severity>
            <expires>2026-01-02T00:00:00Z</expires>
            <description>Imported from CAP XML.</description>
            <area>
                <areaDesc>Test region</areaDesc>
                <polygon>40,-75 40,-74 41,-74 40,-75</polygon>
            </area>
        </info>
    </alert>"""

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

    def test_kml_point_lookat_frames_associated_polygon(self):
        alert = {
            "id": "view-range",
            "event_type": "Test alert",
            "severity": "Extreme",
            "effective": "2026-01-01 00:00",
            "expires": "2026-01-02 00:00",
            "geometries": [{
                "type": "polygon",
                "language": "en-US",
                "event_title": "Test alert",
                "location_name": "Test region",
                "description": "Test description",
                "instruction": "",
                "links": [],
                "coords": [(-75.0, 40.0), (-74.0, 40.0), (-74.0, 41.0), (-75.0, 40.0)],
            }],
        }

        with (
            patch.dict(app.ALERT_CACHE, {"view-range": alert}, clear=True),
            patch.object(app, "NEW_ALERT_IDS", set()),
            patch.object(app, "load_hidden_alert_ids", return_value=set()),
            patch.object(app, "load_custom_alerts", return_value=[]),
        ):
            response = app.app.test_client().get("/alerts.kml")

        root = ET.fromstring(response.data)
        namespace = {"kml": "http://www.opengis.net/kml/2.2"}
        point = root.find(".//kml:Placemark[kml:Point]", namespace)
        self.assertIsNotNone(point)
        lookat = point.find("kml:LookAt", namespace)
        self.assertIsNotNone(lookat)
        self.assertGreater(float(lookat.findtext("kml:range", namespaces=namespace)), 100_000)

    def test_cap_xml_upload_adds_mappable_custom_alert(self):
        saved_alerts = []
        with (
            patch.object(app, "load_custom_alerts", return_value=[]),
            patch.object(app, "save_custom_alerts", side_effect=lambda alerts: saved_alerts.extend(alerts)),
            patch.object(app, "load_hidden_alert_ids", return_value=set()),
        ):
            client = app.app.test_client()
            settings_response = client.get("/settings")
            response = client.post(
                "/settings",
                data={
                    "action": "add_custom_xml",
                    "cap_xml": (BytesIO(self.CAP_XML), "test-alert.xml"),
                },
                content_type="multipart/form-data",
            )

        self.assertIn(b'name="cap_xml"', settings_response.data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("custom_saved=1", response.headers["Location"])
        self.assertEqual(len(saved_alerts), 1)
        imported = saved_alerts[0]
        self.assertTrue(imported["id"].startswith("custom-"))
        self.assertEqual(imported["event_type"], "Imported test alert")
        self.assertEqual(imported["severity"], "Severe")
        self.assertEqual(imported["geometries"][0]["coords"][0], (-75.0, 40.0))
        self.assertTrue(imported["enabled"])

    def test_cap_xml_upload_rejects_invalid_and_unmappable_files(self):
        unmappable_xml = self.CAP_XML.replace(
            b"<polygon>40,-75 40,-74 41,-74 40,-75</polygon>",
            b"",
        )
        with (
            patch.object(app, "load_custom_alerts", return_value=[]),
            patch.object(app, "save_custom_alerts") as save_custom_alerts,
            patch.object(app, "load_hidden_alert_ids", return_value=set()),
            patch.dict(app.ALERT_CACHE, {}, clear=True),
        ):
            client = app.app.test_client()
            for xml_content, error_message in (
                (b"<alert", b"not valid XML"),
                (unmappable_xml, b"mappable polygons or circles"),
            ):
                with self.subTest(error_message=error_message):
                    response = client.post(
                        "/settings",
                        data={
                            "action": "add_custom_xml",
                            "cap_xml": (BytesIO(xml_content), "invalid.xml"),
                        },
                        content_type="multipart/form-data",
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(error_message, response.data)

        save_custom_alerts.assert_not_called()

    def test_custom_alert_time_toggle_preserves_all_by_default_and_filters_when_disabled(self):
        custom_alerts = [
            {"id": "active", "raw_effective": "2026-01-01T10:00:00Z", "raw_expires": "2026-01-01T14:00:00Z"},
            {"id": "future", "raw_effective": "2026-01-01T13:00:00Z", "raw_expires": "2026-01-01T14:00:00Z"},
            {"id": "expired", "raw_effective": "2026-01-01T09:00:00Z", "raw_expires": "2026-01-01T11:00:00Z"},
            {"id": "open-ended", "raw_effective": "N/A", "raw_expires": "N/A"},
            {"id": "disabled", "enabled": False},
        ]
        now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

        with (
            patch.object(app, "load_custom_alerts", return_value=custom_alerts),
            patch.object(app, "load_show_all_custom_alerts", return_value=True),
        ):
            all_alerts = app.get_visible_custom_alerts(now)

        with (
            patch.object(app, "load_custom_alerts", return_value=custom_alerts),
            patch.object(app, "load_show_all_custom_alerts", return_value=False),
        ):
            time_filtered_alerts = app.get_visible_custom_alerts(now)

        self.assertEqual(
            {alert["id"] for alert in all_alerts},
            {"active", "future", "expired", "open-ended"},
        )
        self.assertEqual(
            {alert["id"] for alert in time_filtered_alerts},
            {"active", "open-ended"},
        )

    def test_custom_alert_time_filter_applies_to_map_and_kml_feeds(self):
        def custom_alert(alert_id, start, end):
            return {
                "id": alert_id,
                "event_type": alert_id,
                "severity": "Severe",
                "raw_effective": start,
                "effective": start,
                "raw_expires": end,
                "expires": end,
                "custom": True,
                "enabled": True,
                "geometries": [{
                    "type": "polygon",
                    "language": "en-US",
                    "event_title": alert_id,
                    "location_name": "Test region",
                    "description": "Test description",
                    "instruction": "",
                    "links": [],
                    "coords": [(-75.0, 40.0), (-74.0, 40.0), (-74.0, 41.0)],
                }],
            }

        alerts = [
            custom_alert(
                "active",
                "2026-01-01T10:00:00Z",
                "2026-01-01T14:00:00Z",
            ),
            custom_alert(
                "future",
                "2026-01-01T13:00:00Z",
                "2026-01-01T14:00:00Z",
            ),
        ]
        now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

        with (
            patch.dict(app.ALERT_CACHE, {}, clear=True),
            patch.object(app, "NEW_ALERT_IDS", set()),
            patch.object(app, "load_hidden_alert_ids", return_value=set()),
            patch.object(app, "load_custom_alerts", return_value=alerts),
            patch.object(app, "load_show_all_custom_alerts", return_value=False),
            patch.object(app, "datetime") as datetime_mock,
        ):
            datetime_mock.now.return_value = now
            datetime_mock.fromisoformat = datetime.fromisoformat
            client = app.app.test_client()
            map_response = client.get("/alerts.json")
            kml_response = client.get("/alerts.kml")

        self.assertEqual(
            [alert["id"] for alert in map_response.get_json()["alerts"]],
            ["active"],
        )
        root = ET.fromstring(kml_response.data)
        namespace = {"kml": "http://www.opengis.net/kml/2.2"}
        points = root.findall(".//kml:Point", namespace)
        self.assertEqual(len(points), 1)
        parents = {
            child: parent
            for parent in root.iter()
            for child in parent
        }
        point_names = [
            parents[point].findtext(
                "{http://www.opengis.net/kml/2.2}name",
                default="",
            )
            for point in points
        ]
        self.assertEqual(point_names, ["active"])

    def test_custom_alert_time_toggle_is_saved_without_overwriting_hidden_ids(self):
        with tempfile.TemporaryDirectory() as settings_dir:
            settings_path = os.path.join(settings_dir, "visibility.json")
            with patch.object(app, "VISIBILITY_SETTINGS_PATH", settings_path):
                app.save_show_all_custom_alerts(False)
                app.save_hidden_alert_ids({"hidden-alert"})

                self.assertFalse(app.load_show_all_custom_alerts())
                self.assertEqual(app.load_hidden_alert_ids(), {"hidden-alert"})

    def test_custom_alert_time_toggle_form_saves_filter_choice(self):
        with (
            patch.object(app, "load_custom_alerts", return_value=[]),
            patch.object(app, "load_show_all_custom_alerts", return_value=True),
            patch.object(app, "load_hidden_alert_ids", return_value=set()),
            patch.object(app, "save_show_all_custom_alerts") as save_show_all,
        ):
            client = app.app.test_client()
            settings_response = client.get("/settings")
            response = client.post(
                "/settings",
                data={
                    "action": "set_custom_time_filter",
                    "show_all_custom_alerts": "0",
                },
            )

        self.assertIn(b'name="show_all_custom_alerts"', settings_response.data)
        self.assertIn(b'name="show_all_custom_alerts" value="1" checked', settings_response.data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("custom_saved=1", response.headers["Location"])
        save_show_all.assert_called_once_with(False)

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
