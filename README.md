# ArcGIS Earth FossWarn alert displayer.

A Python application that harvests Common Alerting Protocol (CAP) data from `alerts.kde.org`, processes it into a localized memory cache + disk storage, and streams custom KML files to ArcGIS Earth.

(Vibecoded script)

### Requirements

- The script uses quite a bit of RAM. (approx 1.3~GB)
- Just make sure your computer has enough RAM if you want to load all alerts into ArcGIS Earth.

## Setup and Installation

- Download [ArcGIS Earth](https://www.esri.com/en-us/arcgis/products/arcgis-earth/downloads)
- Download [Python 3.14.3](https://www.python.org/downloads/release/python-3143/)
- Make sure to add it to your paths.

- [Download the latest release](https://github.com/NickIsOnYT/FPAS-KML/releases)

- unzip to a location, then:

```bash
cd to/the/location
```
(or open a command prompt in it)

- run:

```bash
pip install -r requirements.txt
```

- then run:

```bash
python app.py
```

- Open `http://localhost:5000/map` for the live OpenStreetMap alert view. The map refreshes automatically as the local FPAS cache updates.

- Wait for the events to parse

- Open the provided kml file

- To choose which active alerts appear in the KML, open `http://localhost:5000/settings`, select the alerts to show, and click **Save visibility settings**.


## Extra setup/troubleshooting:

- View [Extra setup.md](https://github.com/NickIsOnYT/FPAS-KML/blob/main/Extras/Extra%20setup.md)
- View [Local server setup.md](https://github.com/NickIsOnYT/FPAS-KML/blob/main/Extras/Local%20server%20setup.md)
