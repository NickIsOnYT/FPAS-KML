# Global CAP Weather & Hazard Alert KML Server

A lightweight Python application that harvests global Common Alerting Protocol (CAP) data from `alerts.kde.org`, processes it into a localized memory cache via background threads, and streams custom-styled KML files instantly to Google Earth Pro.

(scripts made with Google Gemini)

## Setup and Installation

- Get [Google Earth Pro](https://www.google.com/earth/about/versions/)
- Get [Python 3.14.3](https://www.python.org/downloads/release/python-3143/)
- Make sure to add it to your paths

- [Download the latest release](https://github.com/NickIsOnYT/FPAS-KML/releases)

- unzip to a location, then

```bash
cd to/the/location
```
(or open a command prompt in it)

- run

```bash
pip install -r requirements.txt
```

- run

```bash
python app.py
```

- Wait for the events to parse

- add `http://localhost:5000/alerts.kml` to your Google Earth network links (Add -> Network Link), or open the .kml file.

- if you want to use the computer you installed this on as a server for the program, please do these steps. otherwise, ignore this.

- Open a seperate Command Prompt, type ipconfig, and look for IPv4 Address under your active network adapter (it usually looks like 192.168.1.X or 10.0.0.X).

- On the secondary computer where you want to view the map, open Google Earth Pro, add a Network Link, and input the Host PC's IP address.
`http://YOUR_HOST_IP_HERE:5000/alerts.kml (eg: http://192.168.1.45:5000/alerts.kml)`

### Troubleshooting for second computer stuff

- For it to connect you may need to adjust your firewall. here's how:

- Type `windows defender firewall` in the windows search.

- In the left-hand sidebar, click on Inbound Rules.

- In the far-right sidebar, click on New Rule...

- Choose Port and click Next.

- Select TCP, and under Specific local ports, type: 5000 and click Next.

- Choose Allow the connection and click Next.

- Keep all three profile checkboxes ticked (Domain, Private, Public) and click Next.

- Give it a name like Python KML Server and click Finish.
