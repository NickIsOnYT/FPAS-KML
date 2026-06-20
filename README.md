# Google Earth FossWarn alert displayer.

A Python application that harvests Common Alerting Protocol (CAP) data from `alerts.kde.org`, processes it into a localized memory cache, and streams custom KML files to Google Earth Pro.

(Script made with Google Gemini)

### Requirements

- The script uses 1.3~ish GB of RAM for me.

## Setup and Installation

- Download [Google Earth Pro](https://www.google.com/earth/about/versions/)
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

- Wait for the events to parse

- add `http://localhost:5000/alerts.kml` to your Google Earth network links (Add -> Network Link), or open the .kml file.


## Extra setup:

- View [Local server setup.md](https://github.com/NickIsOnYT/FPAS-KML/blob/main/Extras/Local%20server%20setup.md)
- View [Extra setup.md](https://github.com/NickIsOnYT/FPAS-KML/blob/main/Extras/Extra%20setup.md)
