# app.py
from flask import Flask, Response
import requests
import simplekml
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import sys

# Import the translation map from our new external translations module
from translations import CATEGORY_MAPPING

app = Flask(__name__)
API_BASE_URL = "https://alerts.kde.org"

# Local database cache
ALERT_CACHE = {}
cache_lock = threading.Lock()

def get_kml_color(severity, alpha=150):
    alpha_hex = f'{alpha:02x}'
    severity = severity.lower()
    if 'minor' in severity: base_color = '00ffff'
    elif 'moderate' in severity: base_color = '00a5ff'
    elif 'severe' in severity: base_color = '0000ff'
    elif 'extreme' in severity: base_color = '800080'
    else: base_color = 'ffffff'
    return alpha_hex + base_color

def calculate_centroid(coords):
    if not coords:
        return (0.0, 0.0)
    unique_coords = list(set(coords))
    avg_lon = sum(c[0] for c in unique_coords) / len(unique_coords)
    avg_lat = sum(c[1] for c in unique_coords) / len(unique_coords)
    return (avg_lon, avg_lat)

def fetch_single_alert(alert_id):
    try:
        r = requests.get(f"{API_BASE_URL}/alert/{alert_id}", timeout=5)
        if r.status_code != 200: return alert_id, None
        
        root = ET.fromstring(r.content)
        info = root.find('.//{*}info')
        if info is None: return alert_id, None

        event = info.findtext('{*}event', 'Alert')
        area = info.find('{*}area')
        
        coords = []
        area_desc = "Unknown Location"
        
        if area is not None:
            area_desc = area.findtext('{*}areaDesc') or "Unknown Location"
            poly_node = area.find('{*}polygon')
            circle_node = area.find('{*}circle')
            
            if poly_node is not None and poly_node.text:
                for pair in poly_node.text.split(): 
                    if ',' in pair:
                        lat, lon = pair.split(',')
                        coords.append((float(lon), float(lat)))
            
            elif circle_node is not None and circle_node.text:
                parts = circle_node.text.split()
                if parts:
                    lat, lon = parts[0].split(',')
                    coords.append((float(lon), float(lat)))
        
        if coords:
            return alert_id, {
                "id": alert_id,
                "event_type": event,
                "title": f"{event} - {area_desc}",
                "severity": info.findtext('{*}severity', 'Unknown'),
                "description": info.findtext('{*}description', 'No description.'),
                "coords": coords
            }
    except Exception:
        pass
    return alert_id, None

def background_alert_harvester():
    global ALERT_CACHE
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
                    cached_ids = list(ALERT_CACHE.keys())
                    for cid in cached_ids:
                        if cid not in active_ids:
                            del ALERT_CACHE[cid]
                
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
                                with cache_lock:
                                    ALERT_CACHE[aid] = data
                            
                            percent = (processed_count / total_new) * 100
                            sys.stdout.write(f"\r[Background Thread] Progress: {percent:.1f}% ({processed_count}/{total_new} completed)")
                            sys.stdout.flush()
                    print("", flush=True)
                                
                print(f"[Background Thread] Sync complete. Local database holds {len(ALERT_CACHE)} valid mapped alerts.", flush=True)
            else:
                print(f"[Background Thread] API error ({r.status_code}), retrying later.", flush=True)
                
        except Exception as e:
            print(f"[Background Thread] Error during harvesting: {e}", flush=True)
            
        time.sleep(300)

@app.route('/alerts.kml')
def serve_kml():
    print(f"Request detected, Serving {len(ALERT_CACHE)} alerts from local memory.", flush=True)
    kml = simplekml.Kml()
    
    with cache_lock:
        cached_alerts = list(ALERT_CACHE.values())
        
    category_folders = {}
    subcategory_folders = {}

    for item in cached_alerts:
        kml_color = get_kml_color(item["severity"])
        center_lon, center_lat = calculate_centroid(item["coords"])
        
        raw_event = str(item.get("event_type", "Alert")).strip()
        lookup_event = raw_event.lower()
        
        specific_alert_name = raw_event.title() if raw_event else "Active Alert"
        
        if lookup_event in CATEGORY_MAPPING:
            category_name = CATEGORY_MAPPING[lookup_event]
        else:
            category_name = specific_alert_name
        
        # 1. Broad Category Folder Assignment
        if category_name not in category_folders:
            category_folders[category_name] = kml.newfolder(name=category_name)
        
        parent_folder = category_folders[category_name]
        
        # 2. Nested Sub-folder Assignment
        sub_folder_key = (category_name, specific_alert_name)
        if sub_folder_key not in subcategory_folders:
            subcategory_folders[sub_folder_key] = parent_folder.newfolder(name=specific_alert_name)
            
        target_folder = subcategory_folders[sub_folder_key]
        
        cap_data_url = f"{API_BASE_URL}/alert/{item['id']}"
        
        popup_content = f"""
        <h3>{item.get("title", "Active Alert")}</h3>
        <p><b>Severity:</b> {str(item.get("severity", "Unknown")).upper()}</p>
        <hr/>
        <p>{item.get("description", "No description provided.")}</p>
        <hr/>
        <p style="font-size: 11px; color: #555555;">
            <b>Sources:</b><br/>
            <a href="{cap_data_url}">View CAP JSON Data</a>
        </p>
        """

        reg = simplekml.Region()
        reg.latlonaltbox.north = center_lat + 0.01
        reg.latlonaltbox.south = center_lat - 0.01
        reg.latlonaltbox.east = center_lon + 0.01
        reg.latlonaltbox.west = center_lon - 0.01
        reg.lod.minlod = 24  
        reg.lod.maxlod = -1

        # --- POLYGON GENERATION ---
        if len(item["coords"]) > 1:
            pol = target_folder.newpolygon(outerboundaryis=item["coords"])
            pol.description = popup_content
            pol.style.polystyle.color = kml_color
            pol.style.linestyle.color = kml_color
            pol.style.balloonstyle.text = "$[description]"
            pol.style.balloonstyle.bgcolor = "ffffffff"

        # --- PIN GENERATION ---
        pin = target_folder.newpoint(name=specific_alert_name, coords=[(center_lon, center_lat)])
        pin.description = popup_content
        pin.region = reg
        
        style_map = simplekml.StyleMap()
        style_map.normalstyle.iconstyle.color = kml_color
        style_map.normalstyle.iconstyle.scale = 0.9
        style_map.normalstyle.labelstyle.scale = 0.8  
        
        style_map.highlightstyle.iconstyle.color = kml_color
        style_map.highlightstyle.iconstyle.scale = 1.1
        style_map.highlightstyle.labelstyle.scale = 1.0 
        
        pin.stylemap = style_map
        pin.style.balloonstyle.text = "$[description]"
        pin.style.balloonstyle.bgcolor = "ffffffff"
        
    return Response(kml.kml(), mimetype='application/vnd.google-earth.kml+xml')

if __name__ == "__main__":
    worker = threading.Thread(target=background_alert_harvester, daemon=True)
    worker.start()
    
    print("Starting KML Presenter server on local network...", flush=True)
    print("Google Earth url: http:ip-address:5000/alerts.kml", flush=True)
    app.run(host='0.0.0.0', port=5000)