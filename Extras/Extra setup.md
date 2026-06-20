## Extra customization

- As a small warning, you will need to adjust these values every time you update the program.


### Ajust the alert pulling frequency

1. Open `app.py` in a text/code editor.
2. Search for the line `time.sleep`. (it should be under `def background_alert_harvester():`)
3. Change the number in the parentheses (`(300)`) to a different number. (Default is 300 seconds (5 minutes))


### Adjust the alert area

- By default, the script pulls all active alerts globally using a wide boundary bounding box. (default: `min_lat=-90&max_lat=90&min_lon=-180&max_lon=180.`)

1. Open `app.py` in a text/code editor.
2. Search for `url = f"{API_BASE_URL}/alert/area?min_lat=-90&max_lat=90&min_lon=-180&max_lon=180"`
3. Use google earth to get the coordinates you want. (in the bottom left, "lat" and "lon" values)
4. Change the "min_lat=#", "max_lat=#", "min_lon=#" & "max_lon=#" to the desired values.