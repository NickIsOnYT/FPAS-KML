## Extra customization

- As a small warning, you will need to adjust these values every time you update the program.


### Adjust Google Earth syncing time (for easier tweaking)

1. Right-click the "FOSS Live Alerts" folder or the added network link.
2. Click on properties, then the refresh tab in the window that opened.
3. Change the time-based refresh to be any interval you want. (I'd recommend setting it to `1 minute` for default use.)
- Keep this in mind if you want to do anything below. When the list refreshes, Google Earth minimizes the folder, so I would recommend setting it to `Once` then refreshing manually for better tweaking.


### Adjust the alert pulling frequency

1. Open `app.py` in a text/code editor.
2. Search for the line `time.sleep`. (it should be under `def background_alert_harvester():`)
3. Change the number in the parentheses (`(15)`) to a different number. (Default is 15 seconds)


### Adjust the alert area

- By default, the script pulls all active alerts globally using a wide boundary bounding box. (default: `min_lat=-90&max_lat=90&min_lon=-180&max_lon=180.`)

1. Open `app.py` in a text/code editor.
2. Search for `url = f"{API_BASE_URL}/alert/area?min_lat=-90&max_lat=90&min_lon=-180&max_lon=180"`
3. Use Google Earth to get the coordinates you want. (in the bottom left, "lat" and "lon" values)
4. Change the "min_lat=#", "max_lat=#", "min_lon=#" & "max_lon=#" to the desired values.

### How to update translations.py yourself

1. Open `translations.py` in a text/code editor. (Visual Studio Code Recommended)

- The side on the left is the name of the alert you want to categorize, the side on the right is the folder you want to categorize the alert to.

2. Copy an alert name from the `Uncategorized Alerts` folder
3. (optional) Translate the text to your language to know what the alert type is.
4. De-capitalize the text. (It will not pick up the name if it contains captial letters) (i recommend [this](https://decapitalize.eu/) site to do that)
5. Add *quotes* (`""`) then paste the de-capitalized text on the left side.
6. Then add a *colon* *space* *quotes* then add text into the quotes.
7. Then add a comma (`,`) at the end of 
8. Make sure the text is at the same place as all the other text. (Aka press tab once at the start of the text if it's not equal to the other text.)

- This will create a folder in Google Earth that contains any alerts that match that text exactly.

- Here's a template you can work off of: `"alert name": "Alert Category",`
