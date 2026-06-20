# app.py
from flask import Flask, Response
import requests
import simplekml
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import sys
import importlib  # Dynamic hot-reloading

# Import the module as an object so we can pass it to importlib.reload
import translations

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

def kml_color_to_hex(kml_color_str):
    """Converts a KML color string (aabbggrr) to a standard CSS hex string (rrggbb)."""
    if len(kml_color_str) == 8:
        b, g, r = kml_color_str[2:4], kml_color_str[4:6], kml_color_str[6:8]
        return f"{r}{g}{b}"
    return "333333"

def calculate_centroid_from_geometries(geometries):
    """Calculates a single global geographic center across a list of geometry coordinate blocks."""
    all_coords = []
    for geom in geometries:
        all_coords.extend(geom["coords"])
    if not all_coords:
        return (0.0, 0.0)
    unique_coords = list(set(all_coords))
    avg_lon = sum(c[0] for c in unique_coords) / len(unique_coords)
    avg_lat = sum(c[1] for c in unique_coords) / len(unique_coords)
    return (avg_lon, avg_lat)

def format_cap_timestamp(ts_str):
    """Formats standard ISO-8601 CAP timestamps into a cleaner, human-readable view."""
    if not ts_str:
        return "N/A"
    try:
        parts = ts_str.replace('T', ' ').split(':')
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
    except Exception:
        pass
    return ts_str

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
            
            cmam_long = None
            cmam_short = None
            for param in info_block.findall('{*}parameter'):
                v_name = param.findtext('{*}valueName', '').strip()
                v_val = param.findtext('{*}value', '').strip()
                if v_name == 'CMAMlongtext':
                    cmam_long = v_val
                elif v_name == 'CMAMtext':
                    cmam_short = v_val
            
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
                                "coords": current_poly_coords
                            })
                
                for circle_node in area.findall('{*}circle'):
                    if circle_node.text:
                        parts = circle_node.text.split()
                        if parts:
                            lat, lon = parts[0].split(',')
                            geometries.append({
                                "type": "circle",
                                "language": lang,
                                "event_title": local_event,
                                "location_name": area_desc,
                                "description": local_description,
                                "instruction": local_instruction,
                                "coords": [(float(lon), float(lat))]
                            })
        
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
    
    try:
        importlib.reload(translations)
    except Exception as reload_error:
        print(f"Warning: Failed to hot-reload translations.py: {reload_error}", flush=True)
        
    kml = simplekml.Kml()
    
    with cache_lock:
        # FIXED: Sort cache descending (reverse=True) so the most recent effective alerts are grouped first
        cached_alerts = sorted(list(ALERT_CACHE.values()), key=lambda x: x.get("raw_effective", ""), reverse=True)
        
    temp_categories = {}
    rendered_polygon_fingerprints = set()
    geolocated_pin_buckets = {}

    for item in cached_alerts:
        kml_color = get_kml_color(item["severity"])
        raw_event = str(item.get("event_type", "Alert")).strip()
        lookup_event = raw_event.lower()
        specific_alert_name = raw_event.title() if raw_event else "Active Alert"
        
        if lookup_event in translations.CATEGORY_MAPPING:
            category_name = translations.CATEGORY_MAPPING[lookup_event]
        else:
            category_name = "Uncategorized Alerts"
        
        if category_name not in temp_categories:
            temp_categories[category_name] = {}
            
        if specific_alert_name not in temp_categories[category_name]:
            temp_categories[category_name][specific_alert_name] = []
            
        cap_data_url = f"{API_BASE_URL}/alert/{item['id']}"
        effective_str = item.get("effective", "N/A")
        expires_str = item.get("expires", "N/A")

        data_groups = {}

        for geom in item.get("geometries", []):
            event_title = geom.get("event_title", specific_alert_name).title()
            desc_text = geom.get("description", "No description available.").strip()
            inst_text = geom.get("instruction", "").strip()
            loc_name = geom.get("location_name", "Unknown Location")
            
            group_key = (event_title, loc_name)
            if group_key not in data_groups:
                data_groups[group_key] = []
            data_groups[group_key].append(geom)

        for (event_title, loc_name), geoms in data_groups.items():
            en_geoms = [g for g in geoms if 'en' in g['language'].lower()]
            fr_geoms = [g for g in geoms if 'fr' in g['language'].lower()]

            pins_to_create = []

            if en_geoms and fr_geoms:
                en_desc = en_geoms[0].get('description', 'No description available.').strip()
                en_inst = en_geoms[0].get('instruction', '').strip()
                fr_desc = fr_geoms[0].get('description', 'No description available.').strip()
                fr_inst = fr_geoms[0].get('instruction', '').strip()

                if en_desc != fr_desc or en_inst != fr_inst:
                    en_body = f"<h4>{event_title}</h4><p>{en_desc}</p>"
                    if en_inst: en_body += f"<h5>Instructions:</h5><p>{en_inst}</p>"
                    fr_body = f"<h4>{fr_geoms[0]['event_title'].title()}</h4><p>{fr_desc}</p>"
                    if fr_inst: fr_body += f"<h5>Instructions:</h5><p>{fr_inst}</p>"

                    pins_to_create.append({"title": f"{event_title} (EN)", "body": en_body, "geoms": en_geoms})
                    pins_to_create.append({"title": f"{event_title} (FR)", "body": fr_body, "geoms": fr_geoms})
                else:
                    combined_body = f"<p>{en_desc}</p>"
                    if en_inst: combined_body += f"<h4>Instructions:</h4><p>{en_inst}</p>"
                    pins_to_create.append({"title": event_title, "body": combined_body, "geoms": geoms})
            else:
                active_geoms = en_geoms if en_geoms else fr_geoms
                if active_geoms:
                    fallback_desc = active_geoms[0].get('description', 'No description available.').strip()
                    fallback_inst = active_geoms[0].get('instruction', '').strip()
                    fallback_body = f"<p>{fallback_desc}</p>"
                    if fallback_inst: fallback_body += f"<h4>Instructions:</h4><p>{fallback_inst}</p>"
                    pins_to_create.append({"title": event_title, "body": fallback_body, "geoms": active_geoms})

            for pin_data in pins_to_create:
                raw_lon, raw_lat = calculate_centroid_from_geometries(pin_data["geoms"])
                coord_bucket = (round(raw_lon, 5), round(raw_lat, 5))
                
                if coord_bucket not in geolocated_pin_buckets:
                    geolocated_pin_buckets[coord_bucket] = []
                    
                geolocated_pin_buckets[coord_bucket].append({
                    "category": category_name,
                    "subcategory": specific_alert_name,
                    "title": pin_data['title'],
                    "loc_name": loc_name,
                    "severity": str(item.get("severity", "Unknown")).upper(),
                    "effective": effective_str,
                    "expires": expires_str,
                    "body": pin_data['body'],
                    "url": cap_data_url,
                    "color": kml_color,
                    "raw_lon": raw_lon,
                    "raw_lat": raw_lat,
                    "geoms": pin_data["geoms"]
                })

    sorted_category_names = sorted(list(temp_categories.keys()))
    category_folders = {}
    subcategory_folders = {}

    for cat_name in sorted_category_names:
        category_folders[cat_name] = kml.newfolder(name=cat_name)
        sorted_sub_names = sorted(list(temp_categories[cat_name].keys()))
        
        for sub_name in sorted_sub_names:
            sub_folder_key = (cat_name, sub_name)
            subcategory_folders[sub_folder_key] = category_folders[cat_name].newfolder(name=sub_name)

    for coord_bucket, stacked_pins in geolocated_pin_buckets.items():
        # Note: geolocated_pin_buckets layout preserves order inherited from the descending cached_alerts sort
        lead_pin = stacked_pins[0]
        target_folder = subcategory_folders[(lead_pin["category"], lead_pin["subcategory"])]

        if len(stacked_pins) > 1:
            consolidated_title = f"{lead_pin['subcategory']} ({len(stacked_pins)})"
            balloon_body_pieces = []
            source_links_by_index = []
            
            for idx, p in enumerate(stacked_pins, 1):
                web_hex = kml_color_to_hex(p['color'])
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
                    f'<li><b>#{idx} ({p["title"]}):</b> <a href="{p["url"]}">View CAP JSON</a></li>'
                )
            
            sources_html = "".join(source_links_by_index)
            
            popup_content = f"""
            <h3>{consolidated_title}</h3>
            <p><b>Region:</b> {lead_pin['loc_name']}</p>
            <hr/>
            {"".join(balloon_body_pieces)}
            <h4 style="margin-top:10px; margin-bottom:5px;">Sources:</h4>
            <ul style="margin-top:0px; padding-left:20px; font-size:11px; color:#555555;">
                {sources_html}
            </ul>
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
            <p style="font-size: 11px; margin-top:0px; color: #555555;">
                <b>{lead_pin['title']}:</b> <a href="{lead_pin['url']}">View CAP JSON Data</a>
            </p>
            """

        reg = simplekml.Region()
        reg.latlonaltbox.north = lead_pin['raw_lat'] + 0.01
        reg.latlonaltbox.south = lead_pin['raw_lat'] - 0.01
        reg.latlonaltbox.east = lead_pin['raw_lon'] + 0.01
        reg.latlonaltbox.west = lead_pin['raw_lon'] - 0.01
        reg.lod.minlod = 24  
        reg.lod.maxlod = -1

        pin = target_folder.newpoint(name=consolidated_title, coords=[(lead_pin['raw_lon'], lead_pin['raw_lat'])])
        pin.description = popup_content
        pin.region = reg
        
        style_map = simplekml.StyleMap()
        style_map.normalstyle.iconstyle.color = lead_pin['color']
        style_map.normalstyle.iconstyle.scale = 0.9
        style_map.normalstyle.labelstyle.scale = 0.8  
        
        style_map.highlightstyle.iconstyle.color = lead_pin['color']
        style_map.highlightstyle.iconstyle.scale = 1.1
        style_map.highlightstyle.labelstyle.scale = 1.0 
        
        pin.stylemap = style_map
        pin.style.balloonstyle.text = "$[description]"
        pin.style.balloonstyle.bgcolor = "ffffffff"

        for p in stacked_pins:
            for geom in p["geoms"]:
                if geom["type"] == "polygon" and len(geom["coords"]) > 1:
                    geo_fingerprint = f"{geom['coords'][0][0]},{geom['coords'][0][1]}_{geom['coords'][-1][0]}_{geom['coords'][-1][1]}_{len(geom['coords'])}"
                    
                    if geo_fingerprint not in rendered_polygon_fingerprints:
                        shape_popup = f"""
                        <h3>{geom['event_title'].title()} - {geom['location_name']}</h3>
                        <p><b>Severity:</b> {p['severity']} &nbsp;|&nbsp; <b>Active:</b> {p['effective']} to {p['expires']}</p>
                        <hr/>
                        <p>{geom['description']}</p>
                        """
                        if geom['instruction']:
                            shape_popup += f"<h4>Instructions:</h4><p>{geom['instruction']}</p>"
                        
                        shape_popup += f"""
                        <hr/>
                        <h4 style="margin-bottom:5px;">Sources:</h4>
                        <p style="font-size: 11px; margin-top:0px; color: #555555;">
                            <b>{geom['event_title'].title()}:</b> <a href="{p['url']}">View CAP JSON Data</a>
                        </p>
                        """
                        
                        pol = target_folder.newpolygon(name=geom['event_title'].title(), outerboundaryis=geom["coords"])
                        pol.description = shape_popup
                        pol.style.polystyle.color = p['color']
                        pol.style.linestyle.color = p['color']
                        pol.style.balloonstyle.text = "$[description]"
                        pol.style.balloonstyle.bgcolor = "ffffffff"
                        
                        rendered_polygon_fingerprints.add(geo_fingerprint)
        
    return Response(kml.kml(), mimetype='application/vnd.google-earth.kml+xml')

if __name__ == "__main__":
    worker = threading.Thread(target=background_alert_harvester, daemon=True)
    worker.start()
    
    print("Starting KML Presenter server on local network...", flush=True)
    print("Google Earth url: http:ip-address:5000/alerts.kml", flush=True)
    app.run(host='0.0.0.0', port=5000)