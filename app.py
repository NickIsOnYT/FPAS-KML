from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for
import requests
import simplekml
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import sys
import importlib  # Dynamic hot-reloading
import math
import gc         # Memory optimization
import os         # Local directory path mapping
import json       # Local disk alert persistence
import re         # Detect links in alert text
import html       # Escape popup link content safely
import uuid
import socket
from datetime import datetime, timezone

import translations

app = Flask(__name__)
API_BASE_URL = "https://alerts.kde.org"

# Local database cache
ALERT_CACHE = {}
cache_lock = threading.Lock()

# Delta Tracking Sets for "New Alerts" folder
NEW_ALERT_IDS = set()
PREVIOUSLY_SEEN_IDS = set()

# Configure persistent local disk cache directory in AppData/Roaming
CACHE_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'FPAS_KML_Cache')
os.makedirs(CACHE_DIR, exist_ok=True)
VISIBILITY_SETTINGS_PATH = os.path.join(CACHE_DIR, 'visibility_settings.json')
CUSTOM_ALERTS_PATH = os.path.join(CACHE_DIR, 'custom_alerts.json')
settings_lock = threading.Lock()
refresh_requested = threading.Event()
REFRESH_INTERVAL_SECONDS = 60

def load_hidden_alert_ids():
    try:
        with open(VISIBILITY_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        hidden_ids = data.get("hidden_alert_ids", [])
        return {str(alert_id) for alert_id in hidden_ids}
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return set()

def save_hidden_alert_ids(hidden_ids):
    temporary_path = f"{VISIBILITY_SETTINGS_PATH}.tmp"
    with settings_lock:
        with open(temporary_path, "w", encoding="utf-8") as f:
            json.dump({"hidden_alert_ids": sorted(hidden_ids)}, f, indent=2)
        os.replace(temporary_path, VISIBILITY_SETTINGS_PATH)

def load_custom_alerts():
    try:
        with open(CUSTOM_ALERTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

def save_custom_alerts(alerts):
    temporary_path = f"{CUSTOM_ALERTS_PATH}.tmp"
    with settings_lock:
        with open(temporary_path, "w", encoding="utf-8") as f:
            json.dump(alerts, f, indent=2, ensure_ascii=False)
        os.replace(temporary_path, CUSTOM_ALERTS_PATH)

def parse_custom_polygon(polygon_text):
    coordinates = []
    for line_number, line in enumerate(polygon_text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            raise ValueError(f"Polygon line {line_number} must be latitude,longitude.")
        latitude, longitude = float(parts[0]), float(parts[1])
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"Polygon line {line_number} has coordinates outside valid ranges.")
        coordinates.append((longitude, latitude))
    if len(coordinates) < 3:
        raise ValueError("A polygon needs at least three coordinate lines.")
    return coordinates

def build_custom_alert(form):
    title = form.get("custom_title", "").strip()
    region = form.get("custom_region", "").strip() or "Custom Region"
    if not title:
        raise ValueError("A custom alert title is required.")
    coordinates = parse_custom_polygon(form.get("custom_polygon", ""))
    start = form.get("custom_start", "").strip() or "N/A"
    end = form.get("custom_end", "").strip() or "N/A"
    urls = [line.strip() for line in form.get("custom_urls", "").splitlines() if line.strip()]
    source_url = form.get("custom_source_url", "").strip()
    geometry = {
        "type": "polygon",
        "language": "en-CA",
        "event_title": title,
        "location_name": region,
        "description": form.get("custom_description", "").strip() or "No description available.",
        "instruction": form.get("custom_instructions", "").strip(),
        "links": urls,
        "coords": coordinates
    }
    return {
        "id": f"custom-{uuid.uuid4().hex}",
        "event_type": title,
        "severity": form.get("custom_severity", "Unknown").strip() or "Unknown",
        "raw_effective": start,
        "effective": start,
        "expires": end,
        "source_url": source_url,
        "custom": True,
        "enabled": True,
        "geometries": [geometry]
    }

def get_alert_display_name(alert):
    event_name = str(alert.get("event_type", "Alert")).strip() or "Alert"
    locations = sorted({
        str(geometry.get("location_name", "Unknown Location")).strip()
        for geometry in alert.get("geometries", [])
        if geometry.get("location_name")
    })
    if locations:
        return f"{event_name} - {', '.join(locations)}"
    return event_name

def get_alert_start_time(alert):
    value = str(alert.get("raw_effective") or alert.get("effective") or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc) if "+" not in value else datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)

def get_unique_alert_links(alert):
    links = []
    seen_urls = set()
    for geometry in alert.get("geometries", []):
        for link in geometry.get("links", []):
            if isinstance(link, (tuple, list)) and len(link) >= 2:
                url = str(link[1]).strip()
            else:
                url = str(link).strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                links.append(url)
    return links

def get_web_alerts():
    hidden_ids = load_hidden_alert_ids()
    with cache_lock:
        alerts = [
            alert for alert_id, alert in ALERT_CACHE.items()
            if str(alert_id) not in hidden_ids
        ]
    alerts.extend(alert for alert in load_custom_alerts() if alert.get("enabled", True))

    web_alerts = []
    for alert in alerts:
        polygon_color, pin_color = get_kml_color_palette(alert.get("severity", "Unknown"))
        geometries = []
        all_coordinates = []
        for geometry in alert.get("geometries", []):
            coordinates = geometry.get("coords", [])
            all_coordinates.extend(coordinates)
            geometries.append({
                "type": geometry.get("type"),
                "coords": coordinates,
                "radius_meters": geometry.get("radius_meters", 0),
                "event_title": geometry.get("event_title", alert.get("event_type", "Alert")),
                "location_name": geometry.get("location_name", "Unknown Location")
            })

        if all_coordinates:
            center_lon = sum(coord[0] for coord in all_coordinates) / len(all_coordinates)
            center_lat = sum(coord[1] for coord in all_coordinates) / len(all_coordinates)
        else:
            center_lon, center_lat = 0, 0

        web_alerts.append({
            "id": str(alert.get("id", "")),
            "event_type": alert.get("event_type", "Alert"),
            "severity": alert.get("severity", "Unknown"),
            "effective": alert.get("effective", "N/A"),
            "expires": alert.get("expires", "N/A"),
            "description": next((
                geometry.get("description", "") for geometry in alert.get("geometries", [])
                if geometry.get("description")
            ), "No description available."),
            "instruction": next((
                geometry.get("instruction", "") for geometry in alert.get("geometries", [])
                if geometry.get("instruction")
            ), ""),
            "source_url": alert.get("source_url") or f"{API_BASE_URL}/alert/{alert.get('id', '')}",
            "links": [
                link for link in get_unique_alert_links(alert)
                if link != (alert.get("source_url") or f"{API_BASE_URL}/alert/{alert.get('id', '')}")
            ],
            "center": [center_lat, center_lon],
            "polygon_color": f"#{kml_color_to_hex(polygon_color)}",
            "pin_color": f"#{kml_color_to_hex(pin_color)}",
            "geometries": geometries
        })

    return sorted(web_alerts, key=get_alert_start_time, reverse=True)

def load_local_disk_cache():
    """Scans files to determine the total count and renders an active progress counter in console."""
    global ALERT_CACHE, PREVIOUSLY_SEEN_IDS
    print(f"[Initialization] Scanning local disk cache: {CACHE_DIR}", flush=True)
    try:
        json_files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
        total_files = len(json_files)
        print(f"[Initialization] Found {total_files} cached alerts on disk to load.", flush=True)
        
        if total_files > 0:
            loaded_count = 0
            for filename in json_files:
                file_path = os.path.join(CACHE_DIR, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data and "id" in data:
                            ALERT_CACHE[data["id"]] = data
                            # Baseline disk alerts as already seen so they aren't tagged as 'New' on boot
                            PREVIOUSLY_SEEN_IDS.add(data["id"])
                except Exception as file_err:
                    print(f"\n[Initialization] Failed to load cache file {filename}: {file_err}", flush=True)
                
                loaded_count += 1
                if loaded_count % 50 == 0 or loaded_count == total_files:
                    percent = (loaded_count / total_files) * 100
                    sys.stdout.write(f"\r[Initialization] Progress: {percent:.1f}% ({loaded_count}/{total_files} completed)")
                    sys.stdout.flush()
            print("", flush=True) 
            
        print(f"[Initialization] Loaded {len(ALERT_CACHE)} alerts from disk into memory successfully.", flush=True)
    except Exception as dir_err:
        print(f"[Initialization] Error accessing cache directory: {dir_err}", flush=True)

def get_kml_color_palette(severity):
    """
    Returns (polygon_color_kml, pin_color_kml) in KML aabbggrr format.
    Pins match the hue family of their polygons but use maximum brightness/contrast.
    """
    sev_clean = str(severity).strip().lower()

    if 'extreme' in sev_clean:
        poly_color = 'b0800080'  # Semi-transparent Purple (#800080)
        pin_color  = 'ffff00ff'  # High-contrast Magenta / Neon Pink (#FF00FF)
    elif 'severe' in sev_clean:
        poly_color = 'b00000ff'  # Semi-transparent Red (#FF0000)
        pin_color  = 'ff8080ff'  # High-contrast Light Coral Red (#FF8080)
    elif 'moderate' in sev_clean:
        poly_color = 'b000a5ff'  # Semi-transparent Orange (#FFA500)
        pin_color  = 'ff00d7ff'  # High-contrast Electric Gold (#FFD700)
    elif 'minor' in sev_clean:
        poly_color = 'b0efae00'  # High-contrast Electric Light Blue (#00AEEF)
        pin_color  = 'ffe6d800'  # Semi-transparent Deep Cyan / Teal (#00D8E6)
    else:  # Handles "UNKNOWN", "Notice", "Informational", etc.
        poly_color = 'b0aaaaaa'  # Semi-transparent Gray (#AAAAAA)
        pin_color  = 'ffffffff'  # Pure White (#FFFFFF)
    return poly_color, pin_color

def kml_color_to_hex(kml_color_str):
    """Converts a KML color string (aabbggrr) to a standard CSS hex string (rrggbb)."""
    if len(kml_color_str) == 8:
        b, g, r = kml_color_str[2:4], kml_color_str[4:6], kml_color_str[6:8]
        return f"{r}{g}{b}"
    return "333333"

def calculate_centroid_from_geometries(geometries):
    all_coords = []
    for geom in geometries:
        all_coords.extend(geom["coords"])
    if not all_coords:
        return (0.0, 0.0)
    
    unique_coords = list(set(tuple(c) for c in all_coords))
    avg_lon = sum(c[0] for c in unique_coords) / len(unique_coords)
    avg_lat = sum(c[1] for c in unique_coords) / len(unique_coords)
    return (avg_lon, avg_lat)

def format_cap_timestamp(ts_str):
    if not ts_str:
        return "N/A"
    try:
        parts = ts_str.replace('T', ' ').split(':')
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
    except Exception:
        pass
    return ts_str

def extract_links_from_text(text):
    if not text:
        return []

    links = []
    seen = set()
    for match in re.finditer(r'(?i)\b(?:https?://|www\.)[^\s<>"\']+', text):
        raw_link = match.group(0).rstrip(".,;:!?)]")
        if raw_link.endswith(")") and raw_link.count("(") < raw_link.count(")"):
            raw_link = raw_link[:-1]

        full_url = raw_link if "://" in raw_link else f"https://{raw_link}"
        if full_url not in seen:
            seen.add(full_url)
            links.append((raw_link, full_url))

    return links

def build_sources_html(title, cap_url, body_text, extra_links=None):
    items = [
        f'<div><b>{html.escape(title)}:</b> <a href="{html.escape(cap_url, quote=True)}" target="_blank">View CAP JSON</a></div>'
    ]

    seen_urls = {cap_url} if cap_url else set()
    for source in extra_links or []:
        if isinstance(source, (tuple, list)) and len(source) >= 2:
            display_text, full_url = source[0], source[1]
        else:
            display_text, full_url = source, source

        if full_url and isinstance(full_url, str) and full_url not in seen_urls:
            seen_urls.add(full_url)
            items.append(
                f'<div><a href="{html.escape(full_url, quote=True)}" target="_blank">{html.escape(str(display_text))}</a></div>'
            )

    for display_text, full_url in extract_links_from_text(body_text):
        if full_url not in seen_urls:
            seen_urls.add(full_url)
            items.append(
                f'<div><a href="{html.escape(full_url, quote=True)}" target="_blank">{html.escape(display_text)}</a></div>'
            )

    return f'<div style="padding-bottom: 40px;">{"".join(items)}</div>'

def fetch_single_alert(alert_id):
    try:
        r = requests.get(f"{API_BASE_URL}/alert/{alert_id}", timeout=5)
        if r.status_code != 200: return alert_id, None
        
        root = ET.fromstring(r.content)
        info_main = root.find('.//{*}info')
        if info_main is None: return alert_id, None

        event = info_main.findtext('{*}event', 'Alert')
        severity = info_main.findtext('{*}severity', 'Unknown')
        
        raw_effective = info_main.findtext('{*}effective', '')
        effective_time = format_cap_timestamp(raw_effective)
        expires_time = format_cap_timestamp(info_main.findtext('{*}expires', ''))
        
        geometries = []
        
        for info_block in root.findall('.//{*}info'):
            lang = info_block.findtext('{*}language', 'en-CA')
            local_event = info_block.findtext('{*}event', event)
            base_description = info_block.findtext('{*}description', 'No description.').strip()
            local_instruction = info_block.findtext('{*}instruction', '').strip()

            info_links = []
            for link_field in ['web', 'contact']:
                link_value = info_block.findtext(f'{{*}}{link_field}', '').strip()
                for display_text, full_url in extract_links_from_text(link_value):
                    if full_url not in {u for _, u in info_links}:
                        info_links.append((display_text, full_url))

            for resource in info_block.findall('{*}resource'):
                uri_value = resource.findtext('{*}uri', '').strip()
                for display_text, full_url in extract_links_from_text(uri_value):
                    if full_url not in {u for _, u in info_links}:
                        info_links.append((display_text, full_url))
            
            cmam_long, cmam_short = None, None
            for param in info_block.findall('{*}parameter'):
                v_name = param.findtext('{*}valueName', '').strip()
                v_val = param.findtext('{*}value', '').strip()
                if v_name == 'CMAMlongtext': cmam_long = v_val
                elif v_name == 'CMAMtext': cmam_short = v_val
            
            local_description = cmam_long or cmam_short or base_description
            
            for area in info_block.findall('{*}area'):
                area_desc = area.findtext('{*}areaDesc', 'Unknown Location')
                
                for poly_node in area.findall('{*}polygon'):
                    if poly_node.text:
                        current_poly_coords = []
                        for pair in poly_node.text.split(): 
                            if ',' in pair:
                                lat, lon = pair.split(',')
                                current_poly_coords.append((float(lon), float(lat)))
                        if current_poly_coords:
                            geometries.append({
                                "type": "polygon",
                                "language": lang,
                                "event_title": local_event,
                                "location_name": area_desc,
                                "description": local_description,
                                "instruction": local_instruction,
                                "links": info_links,
                                "coords": current_poly_coords
                            })
                
                for circle_node in area.findall('{*}circle'):
                    if circle_node.text:
                        parts = circle_node.text.strip().split()
                        if parts and ',' in parts[0]:
                            lat, lon = parts[0].split(',')
                            try:
                                geometries.append({
                                    "type": "circle",
                                    "language": lang,
                                    "event_title": local_event,
                                    "location_name": area_desc,
                                    "description": local_description,
                                    "instruction": local_instruction,
                                    "links": info_links,
                                    "coords": [(float(lon), float(lat))],
                                    "radius_meters": float(parts[1]) if len(parts) > 1 else 0.0
                                })
                            except ValueError:
                                pass
        
        if geometries:
            return alert_id, {
                "id": alert_id,
                "event_type": event,
                "severity": severity,
                "raw_effective": raw_effective or "0000-00-00",
                "effective": effective_time,
                "expires": expires_time,
                "geometries": geometries
            }
    except Exception:
        pass
    return alert_id, None

def background_alert_harvester():
    global ALERT_CACHE, NEW_ALERT_IDS, PREVIOUSLY_SEEN_IDS
    print("[Background Thread] Started alert harvester worker.", flush=True)
    while True:
        try:
            print("\n[Background Thread] Syncing with global server...", flush=True)
            url = f"{API_BASE_URL}/alert/area?min_lat=-90&max_lat=90&min_lon=-180&max_lon=180"
            r = requests.get(url, timeout=10)
            
            if r.status_code == 200:
                active_ids = set(r.json())
                print(f"[Background Thread] Server has {len(active_ids)} active alerts.", flush=True)
                
                with cache_lock:
                    PREVIOUSLY_SEEN_IDS.update(NEW_ALERT_IDS)
                    NEW_ALERT_IDS.clear()

                    cached_ids = list(ALERT_CACHE.keys())
                    for cid in cached_ids:
                        if cid not in active_ids:
                            del ALERT_CACHE[cid]
                            PREVIOUSLY_SEEN_IDS.discard(cid)
                            disk_path = os.path.join(CACHE_DIR, f"{cid}.json")
                            if os.path.exists(disk_path):
                                try: os.remove(disk_path)
                                except Exception: pass
                
                new_ids = [aid for aid in active_ids if aid not in ALERT_CACHE]
                total_new = len(new_ids)
                print(f"[Background Thread] Found {total_new} brand new alerts to parse.", flush=True)
                
                if total_new > 0:
                    processed_count = 0
                    with ThreadPoolExecutor(max_workers=25) as executor:
                        futures = {executor.submit(fetch_single_alert, aid): aid for aid in new_ids}
                        for future in as_completed(futures):
                            aid, data = future.result()
                            processed_count += 1
                            if data:
                                disk_path = os.path.join(CACHE_DIR, f"{aid}.json")
                                try:
                                    with open(disk_path, "w", encoding="utf-8") as f:
                                        json.dump(data, f, ensure_ascii=False)
                                except Exception: pass
                                
                                with cache_lock:
                                    ALERT_CACHE[aid] = data
                                    if aid not in PREVIOUSLY_SEEN_IDS:
                                        NEW_ALERT_IDS.add(aid)
                            
                            if processed_count % 10 == 0 or processed_count == total_new:
                                percent = (processed_count / total_new) * 100
                                sys.stdout.write(f"\r[Background Thread] Progress: {percent:.1f}% ({processed_count}/{total_new} completed)")
                                sys.stdout.flush()
                    print("", flush=True)
                                
                print(f"[Background Thread] Sync complete. Local database holds {len(ALERT_CACHE)} valid mapped alerts.", flush=True)
                gc.collect()
            else:
                print(f"[Background Thread] API error ({r.status_code}), retrying later.", flush=True)
        except Exception as e:
            print(f"[Background Thread] Error during harvesting: {e}", flush=True)
            
        refresh_requested.wait(REFRESH_INTERVAL_SECONDS)
        refresh_requested.clear()

@app.route('/')
def serve_home():
    return render_template_string("""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>FPAS KML</title>
    <style>
        :root { --ink: #eef4f5; --muted: #91a4a8; --panel: #111b1d; --panel-2: #172427; --line: #2a3b3f; --accent: #43d6bd; }
        * { box-sizing: border-box; }
        html, body { margin: 0; min-height: 100%; }
        body { background: #0c1315; color: var(--ink); font-family: "Trebuchet MS", "Segoe UI", sans-serif; padding: 2.5rem 1rem; }
        main { margin: 0 auto; max-width: 760px; }
        .eyebrow { color: var(--accent); font-size: 11px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
        h1 { font-size: clamp(2rem, 6vw, 3.6rem); margin: .35rem 0 .5rem; }
        .intro { color: var(--muted); font-size: 1rem; margin: 0 0 2rem; }
        .links { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
        a { background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--accent); color: var(--ink); display: block; padding: 1.1rem 1.2rem; text-decoration: none; }
        a:hover, a:focus { background: var(--panel-2); border-color: var(--accent); outline: none; }
        .name { display: block; font-size: 1.1rem; font-weight: 700; margin-bottom: .25rem; }
        .detail { color: var(--muted); font-size: .85rem; }
        .refresh-bar { align-items: center; background: var(--panel); border: 1px solid var(--line); display: inline-flex; gap: 9px; margin: 1.25rem 0; max-width: 100%; padding: 8px; }
        .refresh-bar button { background: var(--panel-2); border: 1px solid var(--line); border-radius: 3px; color: var(--ink); cursor: pointer; flex: 0 1 180px; font: inherit; font-weight: 700; min-height: 36px; padding: 7px 10px; }
        .refresh-bar button:hover { border-color: var(--accent); color: var(--accent); }
        .refresh-bar button:focus { border-color: var(--accent); outline: 2px solid #43d6bd33; }
        .refresh-timer { color: var(--muted); font-size: 14px; min-width: 78px; text-align: right; }
        footer { color: var(--muted); font-size: .8rem; margin-top: 2rem; }
        @media (max-width: 560px) { body { padding: 1.25rem .75rem; } h1 { font-size: 2.25rem; } .intro { font-size: .9rem; margin-bottom: 1.25rem; } .links { grid-template-columns: 1fr; } a { padding: .95rem 1rem; } .name { font-size: 1rem; } .detail { font-size: .8rem; } .refresh-bar { margin: 1rem 0; max-width: 100%; } .refresh-bar button { flex-basis: 150px; } .refresh-timer { font-size: 13px; } }
    </style>
</head>
<body>
    <main>
        <div class="eyebrow">FOSSWARN / FPAS</div>
        <h1>FPAS KML</h1>
        <p class="intro">Local alert tools and feeds hosted by this server.</p>
        <div class="refresh-bar"><button id="server-refresh" type="button">Refresh server</button><span class="refresh-timer" id="refresh-countdown">Next: 60s</span></div>
        <nav class="links" aria-label="Hosted pages and feeds">
            <a href="{{ url_for('serve_map') }}"><span class="name">Live alert map</span><span class="detail">OpenStreetMap view with filters and alert details</span></a>
            <a href="{{ url_for('visibility_settings') }}"><span class="name">Alert settings</span><span class="detail">Choose visible alerts and manage custom alerts</span></a>
            <a href="{{ url_for('serve_kml') }}"><span class="name">KML feed</span><span class="detail">Open or add this live feed to ArcGIS Earth</span></a>
            <a href="{{ url_for('serve_alerts_json') }}"><span class="name">JSON feed</span><span class="detail">Machine-readable alert data for integrations</span></a>
        </nav>
        <footer>Map and feeds use the locally harvested FPAS alert cache.</footer>
    </main>
    <script>
    let secondsUntilRefresh = 60;
    let refreshInProgress = false;
    async function refreshServer() {
        if (refreshInProgress) return;
        refreshInProgress = true;
        const button = document.querySelector('#server-refresh');
        button.disabled = true;
        button.textContent = 'Refreshing...';
        try {
            const response = await fetch('{{ url_for('request_server_refresh') }}', { method: 'POST' });
            if (!response.ok) throw new Error('Refresh request failed');
            secondsUntilRefresh = 60;
        } catch (error) {
            button.textContent = 'Refresh failed';
        } finally {
            button.disabled = false;
            button.textContent = 'Refresh server';
            refreshInProgress = false;
        }
    }
    document.querySelector('#server-refresh').addEventListener('click', refreshServer);
    setInterval(() => {
        secondsUntilRefresh = Math.max(0, secondsUntilRefresh - 1);
        document.querySelector('#refresh-countdown').textContent = `Next: ${secondsUntilRefresh}s`;
        if (secondsUntilRefresh === 0) refreshServer();
    }, 1000);
    </script>
</body>
</html>
""")

@app.route('/refresh', methods=['POST'])
def request_server_refresh():
    refresh_requested.set()
    return jsonify({"status": "refresh_requested"})

@app.route('/alerts.json')
def serve_alerts_json():
    return jsonify({"alerts": get_web_alerts(), "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

@app.route('/map')
def serve_map():
    return render_template_string("""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>FPAS Live Alert Map</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <style>
        :root { --ink: #eef4f5; --muted: #91a4a8; --panel: #111b1d; --panel-2: #172427; --line: #2a3b3f; --accent: #43d6bd; }
        * { box-sizing: border-box; }
        html, body { height: 100%; margin: 0; background: #0c1315; color: var(--ink); font-family: "Trebuchet MS", "Segoe UI", sans-serif; }
        .app { display: grid; grid-template-columns: minmax(300px, 370px) 1fr; height: 100%; min-height: 0; }
        aside { background: var(--panel); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; min-width: 0; overflow: hidden; z-index: 500; }
        header { padding: 22px 22px 16px; border-bottom: 1px solid var(--line); }
        .home-link { color: var(--accent); display: inline-block; font-size: 11px; font-weight: 700; margin-bottom: 12px; text-decoration: none; }
        .home-link:hover { text-decoration: underline; }
        .eyebrow { color: var(--accent); font-size: 11px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
        h1 { font-size: 25px; letter-spacing: .01em; margin: 5px 0 3px; }
        .subtitle { color: var(--muted); font-size: 13px; margin: 0; }
        .toolbar { display: grid; gap: 8px; padding: 14px 16px; border-bottom: 1px solid var(--line); }
        input, select, button { background: var(--panel-2); border: 1px solid var(--line); border-radius: 3px; color: var(--ink); font: inherit; min-height: 36px; padding: 7px 10px; }
        input:focus, select:focus, button:focus { border-color: var(--accent); outline: 2px solid #43d6bd33; }
        .toolbar-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        button { cursor: pointer; font-weight: 700; }
        button:hover { border-color: var(--accent); color: var(--accent); }
        .stats { display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; padding: 12px 16px 5px; }
        #alert-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 5px 10px 20px; scrollbar-color: var(--accent) var(--panel-2); scrollbar-width: thin; }
        #alert-list::-webkit-scrollbar, .leaflet-popup-content::-webkit-scrollbar { width: 8px; }
        #alert-list::-webkit-scrollbar-track, .leaflet-popup-content::-webkit-scrollbar-track { background: var(--panel-2); border-radius: 4px; }
        #alert-list::-webkit-scrollbar-thumb, .leaflet-popup-content::-webkit-scrollbar-thumb { background: #347f76; border: 2px solid var(--panel-2); border-radius: 4px; }
        #alert-list::-webkit-scrollbar-thumb:hover, .leaflet-popup-content::-webkit-scrollbar-thumb:hover { background: var(--accent); }
        .alert-row { border-left: 4px solid var(--severity); border-bottom: 1px solid var(--line); cursor: pointer; padding: 12px 10px 11px; transition: background .15s ease; }
        .alert-row:hover, .alert-row.active { background: #1b2a2d; }
        .alert-title { font-size: 14px; font-weight: 700; line-height: 1.3; }
        .alert-meta { color: var(--muted); font-size: 11px; margin-top: 5px; }
        .severity { color: var(--severity); font-size: 10px; font-weight: 700; letter-spacing: .12em; margin-top: 8px; text-transform: uppercase; }
        .empty { color: var(--muted); font-size: 13px; padding: 18px 8px; }
        .map-wrap { position: relative; min-width: 0; }
        #map { height: 100%; width: 100%; background: #a9c4c8; }
        .map-status { background: #101a1ddd; border: 1px solid var(--line); bottom: 16px; color: var(--muted); font-size: 11px; padding: 7px 10px; position: absolute; right: 16px; z-index: 400; }
        .leaflet-popup-content-wrapper, .leaflet-popup-tip { background: #172427; color: var(--ink); }
        .leaflet-popup-content-wrapper { max-height: min(360px, calc(100vh - 32px)); overflow: hidden; }
        .leaflet-popup-content { font-family: "Trebuchet MS", "Segoe UI", sans-serif; font-size: 13px; line-height: 1.45; margin: 14px 16px; max-height: min(332px, calc(100vh - 60px)); max-width: 320px; overflow-y: auto; padding-right: 7px; scrollbar-color: var(--accent) var(--panel-2); scrollbar-width: thin; }
        .popup-title { border-left: 4px solid var(--popup-color); font-size: 17px; font-weight: 700; padding-left: 8px; }
        .popup-alert { border-top: 1px solid var(--line); margin-top: 12px; padding-top: 12px; }
        .popup-alert-heading { border-left: 4px solid var(--popup-color); font-size: 15px; font-weight: 700; padding-left: 8px; }
        .popup-meta { color: var(--muted); font-size: 11px; margin: 8px 0; }
        .popup-section { border-top: 1px solid var(--line); margin-top: 9px; padding-top: 9px; }
        .popup-label { color: var(--accent); font-size: 10px; font-weight: 700; letter-spacing: .12em; margin-bottom: 4px; text-transform: uppercase; }
        .popup-source { border-top: 1px solid var(--line); margin-top: 11px; padding-top: 10px; }
        .popup-source a { color: var(--accent); font-weight: 700; text-decoration: none; }
        .popup-source a:hover { text-decoration: underline; }
        .alert-pin { background: var(--pin-color); border: 2px solid #fff; border-radius: 50% 50% 50% 0; box-shadow: 0 2px 6px #000b; height: 22px; transform: rotate(-45deg); width: 22px; }
        .alert-pin::after { background: #172427; border-radius: 50%; content: ""; height: 6px; left: 6px; position: absolute; top: 6px; width: 6px; }
        @media (max-width: 700px) { .app { grid-template-columns: 1fr; grid-template-rows: 47vh 53vh; } aside { border-bottom: 1px solid var(--line); border-right: 0; grid-row: 2; } .map-wrap { grid-row: 1; } header { padding: 13px 16px 10px; } h1 { font-size: 21px; } .leaflet-popup-content { max-width: calc(100vw - 48px); } }
    </style>
</head>
<body>
<main class="app">
    <aside>
        <header>
            <a class="home-link" href="{{ url_for('serve_home') }}">&larr; Main page</a>
            <div class="eyebrow">FOSSWARN / FPAS</div>
            <h1>Live alert map</h1>
            <p class="subtitle">Active alerts from the global CAP feed</p>
        </header>
        <section class="toolbar" aria-label="Map filters">
            <input id="search" type="search" placeholder="Search event or region">
            <div class="toolbar-row">
                <select id="severity"><option value="all">All severities</option><option value="extreme">Extreme</option><option value="severe">Severe</option><option value="moderate">Moderate</option><option value="minor">Minor</option><option value="unknown">Other</option></select>
                <button id="refresh" type="button">Refresh now</button>
            </div>
        </section>
        <div class="stats"><span id="count">Loading alerts...</span><span id="updated"></span></div>
        <section id="alert-list" aria-live="polite"></section>
    </aside>
    <section class="map-wrap"><div id="map"></div><div class="map-status" id="map-status">Connecting to alert feed...</div></section>
</main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const map = L.map('map', { zoomControl: false, preferCanvas: true }).setView([25, 0], 2);
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap contributors', maxZoom: 19 }).addTo(map);
const layers = L.layerGroup().addTo(map);
let alerts = [];
let hasFitted = false;
let hasLoadedAlerts = false;

function severityKey(value) {
    const severity = String(value || '').toLowerCase();
    return ['extreme', 'severe', 'moderate', 'minor'].find(key => severity.includes(key)) || 'unknown';
}
function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
}
function popupForAlerts(alertsAtCenter) {
    const sections = alertsAtCenter.map((alert, index) => {
        const instructions = alert.instruction ? `<div class="popup-section"><div class="popup-label">Instructions</div>${escapeHtml(alert.instruction).replace(/\\n/g, '<br>')}</div>` : '';
        const urls = (alert.links || []).map(url => `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>`).join('<br>');
        const links = urls ? `<div class="popup-section"><div class="popup-label">Additional links</div>${urls}</div>` : '';
        return `<section class="popup-alert" style="--popup-color:${alert.polygon_color}"><div class="popup-alert-heading">#${index + 1} &middot; ${escapeHtml(alert.event_type)}</div><div class="popup-meta">${escapeHtml(alert.severity)} &middot; Active ${escapeHtml(alert.effective)} to ${escapeHtml(alert.expires)}</div><div class="popup-section"><div class="popup-label">Description</div>${escapeHtml(alert.description).replace(/\\n/g, '<br>')}</div>${instructions}${links}<div class="popup-source"><a href="${escapeHtml(alert.source_url)}" target="_blank" rel="noopener">View source CAP</a></div></section>`;
    }).join('');
    const heading = alertsAtCenter.length > 1 ? `${alertsAtCenter.length} alerts at this location` : escapeHtml(alertsAtCenter[0].event_type);
    return `<div class="popup-card"><div class="popup-title">${heading}</div>${sections}</div>`;
}
function drawAlert(alert, markerPosition, popupContent) {
    const group = L.layerGroup();
    alert.geometries.forEach(geometry => {
        if (geometry.type === 'polygon' && geometry.coords.length > 1) {
            const latLngs = geometry.coords.map(point => [point[1], point[0]]);
            L.polygon(latLngs, { color: alert.polygon_color, weight: 3, opacity: .95, fillColor: alert.polygon_color, fillOpacity: .18 }).bindPopup(popupContent, { maxHeight: 360, maxWidth: 360 }).addTo(group);
        } else if (geometry.type === 'circle' && geometry.coords.length) {
            L.circle([geometry.coords[0][1], geometry.coords[0][0]], { color: alert.polygon_color, weight: 3, opacity: .95, fillColor: alert.polygon_color, fillOpacity: .18, radius: geometry.radius_meters || 0 }).bindPopup(popupContent, { maxHeight: 360, maxWidth: 360 }).addTo(group);
        }
    });
    const marker = L.marker(markerPosition || alert.center, { icon: L.divIcon({ className: '', html: `<div class="alert-pin" style="--pin-color:${alert.pin_color}"></div>`, iconSize: [22, 22], iconAnchor: [11, 22] }) }).bindPopup(popupContent, { maxHeight: 360, maxWidth: 360 });
    marker.addTo(group);
    return group;
}
function visibleAlerts() {
    const query = document.querySelector('#search').value.toLowerCase().trim();
    const selectedSeverity = document.querySelector('#severity').value;
    return alerts.filter(alert => {
        const searchable = `${alert.event_type} ${alert.geometries.map(geometry => geometry.location_name).join(' ')}`.toLowerCase();
        return (!query || searchable.includes(query)) && (selectedSeverity === 'all' || severityKey(alert.severity) === selectedSeverity);
    });
}
function render() {
    layers.clearLayers();
    const filtered = visibleAlerts();
    const list = document.querySelector('#alert-list');
    list.innerHTML = filtered.length ? filtered.map(alert => `<article class="alert-row" data-id="${escapeHtml(alert.id)}" style="--severity:${alert.pin_color}"><div class="alert-title">${escapeHtml(alert.event_type)}</div><div class="alert-meta">${escapeHtml([...new Set(alert.geometries.map(geometry => geometry.location_name))].join(', '))}</div><div class="severity">${escapeHtml(alert.severity)}</div></article>`).join('') : '<div class="empty">No alerts match the current filters.</div>';
    const centerCounts = {};
    filtered.forEach(alert => { const key = alert.center.join(','); centerCounts[key] = (centerCounts[key] || 0) + 1; });
    const centerIndexes = {};
    const alertsByCenter = {};
    filtered.forEach(alert => { const key = alert.center.join(','); (alertsByCenter[key] ||= []).push(alert); });
    filtered.forEach(alert => {
        const centerKey = alert.center.join(',');
        const index = centerIndexes[centerKey] || 0;
        centerIndexes[centerKey] = index + 1;
        const count = centerCounts[centerKey];
        const angle = count > 1 ? (index / count) * Math.PI * 2 : 0;
        const radius = count > 1 ? 0.0015 : 0;
        const markerPosition = [alert.center[0] + Math.sin(angle) * radius, alert.center[1] + Math.cos(angle) * radius];
        const group = drawAlert(alert, markerPosition, popupForAlerts(alertsByCenter[centerKey])).addTo(layers);
        const row = Array.from(list.querySelectorAll('.alert-row')).find(item => item.dataset.id === alert.id);
        row?.addEventListener('click', () => { document.querySelectorAll('.alert-row').forEach(item => item.classList.remove('active')); row.classList.add('active'); map.flyTo(alert.center, Math.max(map.getZoom(), 6), { duration: .7 }); group.eachLayer(layer => layer.openPopup?.()); });
    });
    document.querySelector('#count').textContent = `${filtered.length} of ${alerts.length} alerts shown`;
    document.querySelector('#map-status').textContent = `${filtered.length} mapped alerts`;
    if (!hasFitted && filtered.length) { const bounds = L.latLngBounds(filtered.map(alert => alert.center)); if (bounds.isValid()) map.fitBounds(bounds.pad(.12), { maxZoom: 8 }); hasFitted = true; }
}
async function loadAlerts() {
    document.querySelector('#refresh').disabled = true;
    try {
        const response = await fetch('/alerts.json', { cache: 'no-store' });
        if (!response.ok) throw new Error(`Feed unavailable (${response.status})`);
        const data = await response.json();
        alerts = data.alerts || [];
        hasLoadedAlerts = true;
        document.querySelector('#updated').textContent = `Updated ${new Date(data.updated).toLocaleTimeString()}`;
    } catch (error) {
        console.error('Unable to fetch alert feed:', error);
        document.querySelector('#map-status').textContent = hasLoadedAlerts ? 'Refresh failed; showing previous data' : 'Alert feed unavailable';
        document.querySelector('#count').textContent = hasLoadedAlerts ? `${alerts.length} alerts shown` : 'Unable to load alerts';
        document.querySelector('#refresh').disabled = false;
        return;
    }
    try {
        render();
    } catch (error) {
        console.error('Unable to render alert feed:', error);
        document.querySelector('#map-status').textContent = 'Alert data loaded; map display failed';
        document.querySelector('#count').textContent = `${alerts.length} alerts loaded`;
    }
    finally { document.querySelector('#refresh').disabled = false; }
}
document.querySelector('#search').addEventListener('input', render);
document.querySelector('#severity').addEventListener('change', render);
document.querySelector('#refresh').addEventListener('click', loadAlerts);
loadAlerts();
</script>
</body>
</html>
""")

@app.route('/settings', methods=['GET', 'POST'])
def visibility_settings():
    if request.method == 'POST':
        custom_alerts = load_custom_alerts()
        action = request.form.get("action")
        if action == "add_custom":
            try:
                custom_alerts.append(build_custom_alert(request.form))
                save_custom_alerts(custom_alerts)
                return redirect(url_for('visibility_settings', custom_saved='1'))
            except ValueError as error:
                custom_error = str(error)
        elif action in {"toggle_custom", "delete_custom"}:
            custom_id = request.form.get("custom_id")
            if action == "delete_custom":
                custom_alerts = [alert for alert in custom_alerts if alert.get("id") != custom_id]
            else:
                for alert in custom_alerts:
                    if alert.get("id") == custom_id:
                        alert["enabled"] = request.form.get("enabled") == "1"
            save_custom_alerts(custom_alerts)
            return redirect(url_for('visibility_settings', custom_saved='1'))

        if action == "add_custom" and custom_error:
            pass
        else:
            custom_error = None

        if action == "add_custom" and custom_error:
            with cache_lock:
                active_alerts = sorted(list(ALERT_CACHE.values()), key=get_alert_start_time, reverse=True)
            invalid_field = "custom_polygon" if "polygon" in custom_error.lower() else "custom_title"
            custom_alerts = sorted(custom_alerts, key=get_alert_start_time, reverse=True)
            return render_template_string(SETTINGS_TEMPLATE, active_alerts=active_alerts, hidden_ids=load_hidden_alert_ids(), custom_alerts=custom_alerts, get_alert_display_name=get_alert_display_name, custom_error=custom_error, custom_form=request.form.to_dict(), invalid_field=invalid_field)

        with cache_lock:
            active_ids = {str(alert_id) for alert_id in ALERT_CACHE}

        selected_ids = set(request.form.getlist('visible_alert_ids'))
        hidden_ids = load_hidden_alert_ids()
        hidden_ids.difference_update(active_ids)
        hidden_ids.update(active_ids - selected_ids)
        save_hidden_alert_ids(hidden_ids)
        return redirect(url_for('visibility_settings', saved='1'))

    with cache_lock:
        active_alerts = sorted(
            list(ALERT_CACHE.values()),
            key=get_alert_start_time,
            reverse=True
        )

    hidden_ids = load_hidden_alert_ids()
    custom_alerts = sorted(load_custom_alerts(), key=get_alert_start_time, reverse=True)
    return render_template_string(SETTINGS_TEMPLATE,
        active_alerts=active_alerts, hidden_ids=hidden_ids, custom_alerts=custom_alerts,
        get_alert_display_name=get_alert_display_name, custom_error=None, custom_form={}, invalid_field=None)

SETTINGS_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>FPAS KML visibility</title>
    <style>
        :root { --ink: #eef4f5; --muted: #91a4a8; --panel: #111b1d; --panel-2: #172427; --line: #2a3b3f; --accent: #43d6bd; }
        * { box-sizing: border-box; }
        body { background: #0c1315; color: var(--ink); font-family: "Trebuchet MS", "Segoe UI", sans-serif; line-height: 1.45; margin: 0 auto; max-width: 58rem; padding: 2rem 1rem; }
        h1 { margin-bottom: .4rem; }
        .home-link { color: var(--accent); display: inline-block; font-size: .8rem; font-weight: 700; margin-bottom: 1.1rem; text-decoration: none; }
        .home-link:hover { text-decoration: underline; }
        details { background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: .7rem 1rem; }
        summary { cursor: pointer; font-weight: 600; }
        .alert { border-top: 1px solid var(--line); padding: .7rem 0; }
        .alert:first-of-type { margin-top: .6rem; }
        .alert label { display: block; cursor: pointer; }
        .alert-name { font-weight: 600; }
        .metadata { color: var(--muted); font-size: .9rem; margin-left: 1.6rem; }
        button { background: var(--panel-2); border: 1px solid var(--line); border-radius: 3px; color: var(--ink); cursor: pointer; font-size: 1rem; margin-top: 1rem; padding: .55rem 1rem; }
        button:hover { border-color: var(--accent); color: var(--accent); }
        .saved { color: var(--accent); font-weight: 600; }
        .error { color: #ff8a80; font-weight: 600; }
        .empty { color: var(--muted); margin-top: 1rem; }
        .custom-section { margin-top: 1.5rem; }
        .custom-form { display: grid; gap: .7rem; margin-top: 1rem; }
        .custom-form label { font-weight: 600; }
        .custom-form input, .custom-form select, .custom-form textarea { background: var(--panel-2); border: 1px solid var(--line); border-radius: 3px; color: var(--ink); display: block; margin-top: .25rem; width: 100%; }
        .custom-form textarea { min-height: 80px; resize: vertical; }
        .field-label { align-items: center; display: flex; gap: .35rem; }
        .help-mark { align-items: center; background: #f1f3f4; border: 1px solid #9aa0a6; border-radius: 50%; color: #3c4043; cursor: help; display: inline-flex; font-size: .75rem; font-weight: 700; height: 1.25rem; justify-content: center; line-height: 1.1rem; width: 1.25rem; }
        .help-tip { background: #202124; border-radius: 3px; color: #fff; display: inline-block; font-size: .75rem; font-weight: 400; margin-left: .4rem; max-width: 260px; padding: .35rem .5rem; vertical-align: middle; }
        .invalid input, .invalid textarea, .invalid select { border: 2px solid #b3261e; }
        .invalid .help-mark { border-color: #b3261e; color: #b3261e; }
        .custom-grid { display: grid; gap: .7rem; grid-template-columns: 1fr 1fr; }
        .custom-item { border-top: 1px solid var(--line); margin-top: .7rem; padding: .7rem 0; }
        .custom-item form { display: inline; margin-right: .5rem; }
        .disabled { color: #777; }
        code { background: var(--panel-2); color: var(--accent); padding: .1rem .25rem; }
        @media (max-width: 600px) {
            body { margin: 1rem auto; padding: 0 .75rem; }
            h1 { font-size: 1.55rem; }
            details { padding: .6rem .7rem; }
            .custom-grid { grid-template-columns: 1fr; }
            .custom-form button, body > button { width: 100%; }
            .custom-item form { display: block; margin: .45rem 0 0; }
            .custom-item form button { margin-top: 0; width: 100%; }
            .metadata { font-size: .8rem; margin-left: 0; }
        }
    </style>
</head>
<body>
    <a class="home-link" href="{{ url_for('serve_home') }}">&larr; Main page</a>
    <h1>KML alert visibility</h1>
    <p>Choose which active alerts should appear in the KML file.</p>
    {% if request.args.get('saved') %}<p class="saved">Visibility settings saved.</p>{% endif %}
    {% if request.args.get('custom_saved') %}<p class="saved">Custom alert settings saved.</p>{% endif %}
    {% if custom_error %}<p class="error">{{ custom_error }}</p>{% endif %}
    <form method="post">
        <details>
            <summary>Active alerts ({{ active_alerts|length }})</summary>
            {% for alert in active_alerts %}
            <div class="alert">
                <label>
                    <input type="checkbox" name="visible_alert_ids" value="{{ alert['id'] }}"{% if alert['id']|string not in hidden_ids %} checked{% endif %}>
                    <span class="alert-name">{{ get_alert_display_name(alert) }}</span>
                </label>
                <div class="metadata">ID: {{ alert['id'] }} | Severity: {{ alert.get('severity', 'Unknown') }} | Expires: {{ alert.get('expires', 'N/A') }}</div>
            </div>
            {% else %}
            <p class="empty">No active alerts have been fetched yet.</p>
            {% endfor %}
        </details>
        <button type="submit">Save visibility settings</button>
    </form>
    <details class="custom-section">
        <summary>Custom alerts ({{ custom_alerts|length }})</summary>
        <p>Create map/KML-only alerts for testing or demonstrations. They are stored separately from FPAS alerts.</p>
        <form class="custom-form" method="post">
            <input type="hidden" name="action" value="add_custom">
            <label class="{% if invalid_field == 'custom_title' %}invalid{% endif %}"><span class="field-label">Title <span class="help-mark" title="Example: Demonstration Flood Warning">?</span></span><input name="custom_title" required value="{{ custom_form.get('custom_title', '') }}" placeholder="Example: Demonstration Flood Warning"></label>
            <div class="custom-grid">
                <label><span class="field-label">Region <span class="help-mark" title="Example: Test Valley">?</span></span><input name="custom_region" value="{{ custom_form.get('custom_region', '') }}" placeholder="Example: Test Valley"></label>
                <label><span class="field-label">Severity <span class="help-mark" title="Choose the severity shown by the map color">?</span></span><select name="custom_severity">{% for option in ['Extreme', 'Severe', 'Moderate', 'Minor', 'Unknown'] %}<option{% if custom_form.get('custom_severity', 'Moderate') == option %} selected{% endif %}>{{ option }}</option>{% endfor %}</select></label>
                <label><span class="field-label">Start date/time <span class="help-mark" title="Example: 2026-09-02 14:00">?</span></span><input name="custom_start" value="{{ custom_form.get('custom_start', '') }}" placeholder="2026-09-02 14:00"></label>
                <label><span class="field-label">End date/time <span class="help-mark" title="Example: 2026-09-02 18:00">?</span></span><input name="custom_end" value="{{ custom_form.get('custom_end', '') }}" placeholder="2026-09-02 18:00"></label>
            </div>
            <label><span class="field-label">Description <span class="help-mark" title="Example: Heavy rain is expected in the valley.">?</span></span><textarea name="custom_description" placeholder="What is happening?">{{ custom_form.get('custom_description', '') }}</textarea></label>
            <label><span class="field-label">Instructions <span class="help-mark" title="Example: Avoid low-lying roads and follow local guidance.">?</span></span><textarea name="custom_instructions" placeholder="What should people do?">{{ custom_form.get('custom_instructions', '') }}</textarea></label>
            <label class="{% if invalid_field == 'custom_polygon' %}invalid{% endif %}"><span class="field-label">Polygon coordinates <span class="help-mark" title="One latitude,longitude point per line. Example: 45.0000,-70.0000">?</span></span><textarea name="custom_polygon" required placeholder="45.0000,-70.0000&#10;45.0000,-70.1000&#10;45.1000,-70.0000">{{ custom_form.get('custom_polygon', '') }}</textarea></label>
            <label><span class="field-label">Custom URLs <span class="help-mark" title="Example: https://example.com/info, one complete URL per line">?</span></span><textarea name="custom_urls" placeholder="One URL per line">{{ custom_form.get('custom_urls', '') }}</textarea></label>
            <label><span class="field-label">Custom source URL <span class="help-mark" title="Example: https://example.com/source">?</span></span><input name="custom_source_url" type="url" value="{{ custom_form.get('custom_source_url', '') }}" placeholder="https://example.com/source"></label>
            <button type="submit">Add custom alert</button>
        </form>
        {% for alert in custom_alerts %}
        <div class="custom-item{% if not alert.get('enabled', True) %} disabled{% endif %}">
            <b>{{ alert.get('event_type', 'Custom alert') }}</b> - {{ alert.get('geometries', [{}])[0].get('location_name', 'Custom Region') }}<br>
            <small>{{ alert.get('severity', 'Unknown') }} | {{ alert.get('effective', 'N/A') }} to {{ alert.get('expires', 'N/A') }}</small><br>
            <form method="post"><input type="hidden" name="action" value="toggle_custom"><input type="hidden" name="custom_id" value="{{ alert['id'] }}"><input type="hidden" name="enabled" value="{{ 0 if alert.get('enabled', True) else 1 }}"><button type="submit">{{ 'Disable' if alert.get('enabled', True) else 'Enable' }}</button></form>
            <form method="post"><input type="hidden" name="action" value="delete_custom"><input type="hidden" name="custom_id" value="{{ alert['id'] }}"><button type="submit">Delete</button></form>
        </div>
        {% else %}<p class="empty">No custom alerts created.</p>{% endfor %}
    </details>
</body>
<script>
document.querySelectorAll('.help-mark').forEach(mark => {
    mark.setAttribute('role', 'button');
    mark.setAttribute('tabindex', '0');
    const showHelp = () => {
        document.querySelectorAll('.help-tip').forEach(tip => tip.remove());
        const tip = document.createElement('span');
        tip.className = 'help-tip';
        tip.textContent = mark.title;
        mark.after(tip);
    };
    mark.addEventListener('click', showHelp);
    mark.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); showHelp(); }
    });
});
</script>
</html>
"""

@app.route('/alerts.kml')
def serve_kml():
    hidden_ids = load_hidden_alert_ids()
    
    try:
        importlib.reload(translations)
    except Exception as reload_error:
        print(f"Warning: Failed to hot-reload translations.py: {reload_error}", flush=True)
        
    kml = simplekml.Kml(name="FOSSWARN Active Alerts")
    
    with cache_lock:
        cached_alerts = sorted(
            [alert for alert_id, alert in ALERT_CACHE.items() if str(alert_id) not in hidden_ids],
            key=lambda x: x.get("raw_effective", ""),
            reverse=True
        )
        current_new_ids = set(NEW_ALERT_IDS)

    cached_alerts.extend(alert for alert in load_custom_alerts() if alert.get("enabled", True))
    cached_alerts.sort(key=lambda alert: alert.get("raw_effective", ""), reverse=True)

    print(f"Request detected, Serving {len(cached_alerts)} visible alerts from local memory.", flush=True)
        
    active_categories_and_subs = {} 
    rendered_polygon_fingerprints = set()
    geolocated_pin_buckets = {}

    for item in cached_alerts:
        poly_kml_color, pin_kml_color = get_kml_color_palette(item["severity"])
        raw_event = str(item.get("event_type", "Alert")).strip()
        
        cap_data_url = item.get("source_url") or f"{API_BASE_URL}/alert/{item['id']}"
        effective_str = item.get("effective", "N/A")
        expires_str = item.get("expires", "N/A")
        is_new_alert = item["id"] in current_new_ids

        data_groups = {}
        for geom in item.get("geometries", []):
            event_title = geom.get("event_title", raw_event).title()
            loc_name = geom.get("location_name", "Unknown Location")
            
            group_key = (event_title, loc_name)
            if group_key not in data_groups:
                data_groups[group_key] = []
            data_groups[group_key].append(geom)

        for (event_title, loc_name), geoms in data_groups.items():
            desc_groups = {}
            for g in geoms:
                g_lang = g['language'].upper()
                g_title = g.get('event_title', raw_event).title()
                g_desc = g.get('description', 'No description available.').strip()
                g_inst = g.get('instruction', '').strip()
                
                if g_desc not in desc_groups:
                    desc_groups[g_desc] = {
                        "title": g_title,
                        "langs": set(),
                        "instructions": {}, 
                        "geoms": [],
                        "links": []
                    }
                
                desc_groups[g_desc]["langs"].add(g_lang)
                desc_groups[g_desc]["geoms"].append(g)
                for link in g.get("links", []):
                    if link not in desc_groups[g_desc]["links"]:
                        desc_groups[g_desc]["links"].append(link)
                if g_inst:
                    if g_inst not in desc_groups[g_desc]["instructions"]:
                        desc_groups[g_desc]["instructions"][g_inst] = set()
                    desc_groups[g_desc]["instructions"][g_inst].add(g_lang)

            pins_to_create = []

            for desc_text, info in desc_groups.items():
                body = f"<p>{desc_text}</p>"
                
                if info["instructions"]:
                    body += "<h4>Instructions:</h4>"
                    for inst_text, inst_langs in sorted(info["instructions"].items()):
                        inst_lang_label = "/".join(sorted(list(inst_langs)))
                        clean_text = inst_text.replace("\n", "<br/>")
                        body += f"<p><b>({inst_lang_label}):</b> {clean_text}</p>"
                
                pins_to_create.append({
                    "title": info['title'],  
                    "base_title": info['title'],
                    "body": body,
                    "links": info.get("links", []),
                    "geoms": info["geoms"]
                })

            for pin_data in pins_to_create:
                raw_lon, raw_lat = calculate_centroid_from_geometries(pin_data["geoms"])
                coord_bucket = (round(raw_lon, 5), round(raw_lat, 5))
                
                target_subcategory_string = str(pin_data['base_title']).strip()
                lookup_event = target_subcategory_string.lower()
                
                if lookup_event in translations.CATEGORY_MAPPING:
                    category_name = translations.CATEGORY_MAPPING[lookup_event]
                else:
                    base_lookup = raw_event.lower()
                    if base_lookup in translations.CATEGORY_MAPPING:
                        category_name = translations.CATEGORY_MAPPING[base_lookup]
                    else:
                        category_name = "Uncategorized Alerts"
                
                if coord_bucket not in geolocated_pin_buckets:
                    geolocated_pin_buckets[coord_bucket] = []
                    
                geolocated_pin_buckets[coord_bucket].append({
                    "category": category_name,
                    "subcategory": target_subcategory_string,
                    "is_new": is_new_alert,
                    "title": pin_data['title'],
                    "loc_name": loc_name,
                    "severity": str(item.get("severity", "Unknown")).upper(),
                    "effective": effective_str,
                    "expires": expires_str,
                    "body": pin_data['body'],
                    "links": pin_data.get('links', []),
                    "url": cap_data_url,
                    "poly_color": poly_kml_color,
                    "pin_color": pin_kml_color,
                    "raw_lon": raw_lon,
                    "raw_lat": raw_lat,
                    "geoms": pin_data["geoms"]
                })

    for coord_bucket, stacked_pins in geolocated_pin_buckets.items():
        lead_pin = stacked_pins[0]
        if not lead_pin["is_new"]:
            c_name = lead_pin["category"]
            s_name = lead_pin["subcategory"]
            if c_name not in active_categories_and_subs:
                active_categories_and_subs[c_name] = set()
            active_categories_and_subs[c_name].add(s_name)

    # Initialize folders
    new_alerts_folder = kml.newfolder(name="New Alerts")
    new_subfolders = {}
    
    category_folders = {}
    subcategory_folders = {}
    
    # Store unique region names per subfolder for generating folder descriptions
    folder_region_trackers = {}
    
    sorted_active_categories = sorted(list(active_categories_and_subs.keys()))
    for cat_name in sorted_active_categories:
        category_folders[cat_name] = kml.newfolder(name=cat_name)
        
        sorted_active_subs = sorted(list(active_categories_and_subs[cat_name]))
        for sub_name in sorted_active_subs:
            sub_folder_key = (cat_name, sub_name)
            sub_folder_obj = category_folders[cat_name].newfolder(name=sub_name)
            subcategory_folders[sub_folder_key] = sub_folder_obj
            folder_region_trackers[sub_folder_obj] = set()

    for coord_bucket, stacked_pins in geolocated_pin_buckets.items():
        lead_pin = stacked_pins[0]
        
        # Determine target folder hierarchy
        if lead_pin["is_new"]:
            new_sub_name = lead_pin["subcategory"]
            if new_sub_name not in new_subfolders:
                sub_f = new_alerts_folder.newfolder(name=new_sub_name)
                new_subfolders[new_sub_name] = sub_f
                folder_region_trackers[sub_f] = set()
            target_folder = new_subfolders[new_sub_name]
        else:
            target_folder = subcategory_folders[(lead_pin["category"], lead_pin["subcategory"])]

        # Collect regions into the parent subfolder's tracker
        for p in stacked_pins:
            if p.get("loc_name"):
                folder_region_trackers[target_folder].add(p["loc_name"])

        if len(stacked_pins) > 1:
            consolidated_title = f"{lead_pin['subcategory']} ({len(stacked_pins)})"
            balloon_body_pieces = []
            source_links_by_index = []
            
            for idx, p in enumerate(stacked_pins, 1):
                web_hex = kml_color_to_hex(p['poly_color'])
                section = f"""
                <div style="border-left: 4px solid #{web_hex}; padding-left: 8px; margin-bottom: 12px;">
                    <strong style="font-size:14px;">#{idx}: {p['title']}</strong><br/>
                    <small><b>Severity:</b> {p['severity']} &nbsp;|&nbsp; <b>Active:</b> {p['effective']} to {p['expires']}</small>
                    {p['body']}
                </div>
                <hr style="border: 0; border-top: 1px dashed #ccc;"/>
                """
                balloon_body_pieces.append(section)
                source_links_by_index.append(
                    build_sources_html(f"#{idx} ({p['title']})", p["url"], p["body"], p.get("links", []))
                )
            
            popup_content = f"""
            <h3>{consolidated_title}</h3>
            <p><b>Region:</b> {lead_pin['loc_name']}</p>
            <hr/>
            {"".join(balloon_body_pieces)}
            <h4 style="margin-top:10px; margin-bottom:5px;">Sources:</h4>
            <div style="margin-top:0px; padding-left:8px; font-size:11px; color:#555555;">
                {"".join(source_links_by_index)}
            </div>
            """
        else:
            consolidated_title = lead_pin['title']
            popup_content = f"""
            <h3>{lead_pin['title']}</h3>
            <p><b>Region:</b> {lead_pin['loc_name']}</p>
            <p>
                <b>Severity:</b> {lead_pin['severity']} &nbsp;|&nbsp; 
                <b>Active:</b> {lead_pin['effective']} to {lead_pin['expires']}
            </p>
            <hr/>
            {lead_pin['body']}
            <hr/>
            <h4 style="margin-bottom:5px;">Sources:</h4>
            <div style="margin-top:0px; padding-left:8px; padding-bottom:15px; font-size:11px; color:#555555;">
                {build_sources_html(lead_pin['title'], lead_pin['url'], lead_pin['body'], lead_pin.get('links', []))}
            </div>
            """

        # Point Pin Definition
        pin = target_folder.newpoint(name=consolidated_title, coords=[(lead_pin['raw_lon'], lead_pin['raw_lat'])])
        pin.description = popup_content
        
        # StyleMap setup for pins
        style_map = simplekml.StyleMap()
        
        style_map.normalstyle.iconstyle.color = lead_pin['pin_color']
        style_map.normalstyle.iconstyle.scale = 1.2
        style_map.normalstyle.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/pushpin/wht-pushpin.png'
        style_map.normalstyle.labelstyle.scale = 0.9
        
        style_map.highlightstyle.iconstyle.color = lead_pin['pin_color']
        style_map.highlightstyle.iconstyle.scale = 1.7
        style_map.highlightstyle.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/pushpin/wht-pushpin.png'
        style_map.highlightstyle.labelstyle.scale = 1.0
        
        pin.stylemap = style_map

        for p in stacked_pins:
            for geom in p["geoms"]:
                if geom["type"] == "polygon" and len(geom["coords"]) > 1:
                    geo_fingerprint = f"{geom['coords'][0][0]},{geom['coords'][0][1]}_{geom['coords'][-1][0]}_{geom['coords'][-1][1]}_{len(geom['coords'])}"
                    
                    if geo_fingerprint not in rendered_polygon_fingerprints:
                        shape_popup = f"""
                        <h3>{p['title']}</h3>
                        <p><b>Region:</b> {geom['location_name']}</p>
                        <p><b>Severity:</b> {p['severity']} &nbsp;|&nbsp; <b>Active:</b> {p['effective']} to {p['expires']}</p>
                        <hr/>
                        {p['body']}
                        <hr/>
                        <h4 style="margin-bottom:5px;">Sources:</h4>
                        <div style="margin-top:0px; padding-left:8px; font-size:11px; color:#555555;">
                            {build_sources_html(p['title'], p['url'], p['body'], p.get('links', []))}
                        </div>
                        """
                        
                        pol = target_folder.newpolygon(name=geom['event_title'].title(), outerboundaryis=geom["coords"])
                        pol.description = shape_popup
                        pol.style.polystyle.color = p['poly_color']
                        pol.style.linestyle.color = p['poly_color']
                        pol.style.linestyle.width = 3.0
                        
                        rendered_polygon_fingerprints.add(geo_fingerprint)
                        
                elif geom["type"] == "circle":
                    lon_c, lat_c = geom["coords"][0]
                    radius = geom.get("radius_meters", 0.0)
                    
                    if radius > 0:
                        deg_radius = radius / 111000.0 
                        circle_coords = []
                        
                        for i in range(37): 
                            angle = math.radians(i * 10)
                            p_lon = lon_c + (deg_radius * math.cos(angle)) / math.cos(math.radians(lat_c))
                            p_lat = lat_c + (deg_radius * math.sin(angle))
                            circle_coords.append((p_lon, p_lat))
                            
                        shape_popup = f"""
                        <h3>{p['title']}</h3>
                        <p><b>Region:</b> {geom['location_name']}</p>
                        <p><b>Severity:</b> {p['severity']} &nbsp;|&nbsp; <b>Active:</b> {p['effective']} to {p['expires']}</p>
                        <hr/>
                        {p['body']}
                        <hr/>
                        <h4 style="margin-bottom:5px;">Sources:</h4>
                        <div style="margin-top:0px; padding-left:8px; font-size:11px; color:#555555;">
                            {build_sources_html(p['title'], p['url'], p['body'], p.get('links', []))}
                        </div>
                        """
                        
                        pol = target_folder.newpolygon(name=geom['event_title'].title(), outerboundaryis=circle_coords)
                        pol.description = shape_popup
                        pol.style.polystyle.color = p['poly_color']
                        pol.style.linestyle.color = p['poly_color']
                        pol.style.linestyle.width = 3.0

    # Populate descriptions for all active subfolders listing affected regions on a single line
    for folder_obj, regions_set in folder_region_trackers.items():
        if regions_set:
            sorted_regions = sorted(list(regions_set))
            horizontal_region_str = ", ".join([html.escape(r) for r in sorted_regions])
            folder_obj.description = f"Regions: {horizontal_region_str}"

    output_kml_data = kml.kml()
    
    del kml
    del category_folders
    del subcategory_folders
    del new_subfolders
    del folder_region_trackers
    del geolocated_pin_buckets
    gc.collect()  
    
    return Response(output_kml_data, mimetype='application/vnd.google-earth.kml+xml')

if __name__ == "__main__":
    print("[Boot] Initializing system modules...", flush=True)
    load_local_disk_cache()
    print("[Boot] System baseline ready.\n", flush=True)
    
    worker = threading.Thread(target=background_alert_harvester, daemon=True)
    worker.start()
    
    print("Starting KML Presenter server on local network...", flush=True)
    print("(Internal) KML URL: http://localhost:5000/alerts.kml", flush=True)
    print("(Internal) Page Index: http://localhost:5000/", flush=True)
    try:
        network_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        network_socket.connect(("10.255.255.255", 1))
        lan_ip = network_socket.getsockname()[0]
        network_socket.close()
        print(f"External URL: http://{lan_ip}:5000/", flush=True)
    except OSError:
        print("External URL: use this computer's Wi-Fi IPv4 address with port 5000", flush=True)
    app.run(host='0.0.0.0', port=5000)