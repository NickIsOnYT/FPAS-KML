## Set up a local server (easy)
- The local network IP should be in the console when you boot up the script. (It usually looks like `192.168.1.X` or `10.0.0.X`).
- open the included `live_alerts_server.kml` file in a text editor.
- Chnage the line labeled `<href>http://10.0.0.212:5000/alerts.kml</href>` to the local network ip. (`http://ip_here:5000/alerts.kml`)
- You can now open the KML file in ArcGIS Earth, and it should be fully configured to the default.


## Open the webpages on a phone or other device
- Start the script on the host computer.
- Connect the device to the same Wi-Fi network as the host computer. Guest Wi-Fi networks may block devices from communicating with each other.
- Use the `External URL` shown in the console. For example: `http://10.0.0.212:5000/`
- From the page index, you can open the available webpages and feeds:
	- Main page: `http://YOUR_HOST_IP_HERE:5000/`
	- Live map: `http://YOUR_HOST_IP_HERE:5000/map`
	- Alert settings: `http://YOUR_HOST_IP_HERE:5000/settings`
	- KML feed: `http://YOUR_HOST_IP_HERE:5000/alerts.kml`
	- JSON feed: `http://YOUR_HOST_IP_HERE:5000/alerts.json`
- Do not use `http://localhost:5000` on the external device. `localhost` means the device itself, not the host computer.

### Windows firewall
If the page still cannot connect, open PowerShell as Administrator on the host computer and run:
```powershell
New-NetFirewallRule -DisplayName "FPAS KML Flask 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private
```
Then restart the script and try the `External URL` again. Only allow the `Public` firewall profile if you understand the security implications of exposing the server on public networks.


## Set up a local server (manual)
- The local network IP should be in the console when you boot up the script. (It usually looks like `192.168.1.X` or `10.0.0.X`)
- On the secondary computer where you want to view the map, open ArcGIS Earth, click the plus button, click `Add from Path`, select the `KML/KMZ` type, and input the Host PC's IP address in the "Add from Path" text field.
`http://YOUR_HOST_IP_HERE:5000/alerts.kml (eg: http://192.168.1.45:5000/alerts.kml)`


### Troubleshooting
- For it to connect, you may need to adjust your firewall. Here's how:
1. Type `windows defender firewall` into Windows search.
2. On the left-hand sidebar, click on `Advanced settings`.
3. In the left-hand sidebar, click on `Inbound Rules`.
4. In the far-right sidebar, click on `New Rule...`.
5. Choose `Port` and click Next.
6. Select `TCP`, and under Specific local ports, type: `5000` and click Next.
7. Choose `Allow the connection` and click Next.
8. Keep all three profile checkboxes ticked (Domain, Private, Public) and click Next.
9. Give it a name like `Python KML Server` and click Finish.
