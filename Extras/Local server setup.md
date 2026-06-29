## Set up a local server (easy)
- The local network IP should be in the console when you boot up the script. (It usually looks like `192.168.1.X` or `10.0.0.X`).
- open the included `live_alerts_server.kml` file in a text editor.
- Chnage the line labeled `<href>http://10.0.0.212:5000/alerts.kml</href>` to the local network ip. (ex: `http://ip_here:5000/alerts.kml`)
- You can now open the KML file in Google Earth, and it should be fully configured to the default.


## Set up a local server (manual)
- The local network IP should be in the console when you boot up the script. (It usually looks like `192.168.1.X` or `10.0.0.X`)
- On the secondary computer where you want to view the map, open Google Earth Pro, add a Network Link, and input the Host PC's IP address.
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
