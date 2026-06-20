# Global CAP Weather & Hazard Alert KML Server

A lightweight Python application that harvests global Common Alerting Protocol (CAP) data from `alerts.kde.org`, processes it into a localized memory cache via background threads, and streams custom-styled KML files instantly to Google Earth Pro.

(scripts made with Google Gemini)


## Setup and Installation

- Download [Google Earth Pro](https://www.google.com/earth/about/versions/)
- Download [Python 3.14.3](https://www.python.org/downloads/release/python-3143/)
- Make sure to add it to your paths

- [Download the latest release](https://github.com/NickIsOnYT/FPAS-KML/releases)

- unzip to a location, then

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

- View [Local server setup.md](example.com)
- View [Extra setup.md](example.com)
