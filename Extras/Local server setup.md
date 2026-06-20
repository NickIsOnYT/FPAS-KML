## Set up a local server

- The local network IP should be in the console when you boot up the app. (It usually looks like `192.168.1.X` or `10.0.0.X`).

- On the secondary computer where you want to view the map, open Google Earth Pro, add a Network Link, and input the Host PC's IP address.
`http://YOUR_HOST_IP_HERE:5000/alerts.kml (eg: http://192.168.1.45:5000/alerts.kml)`


### Troubleshooting

- For it to connect you may need to adjust your firewall. here's how:

1. Type `windows defender firewall` in the windows search.

2. In the left-hand sidebar, click on Inbound Rules.

3. In the far-right sidebar, click on New Rule...

4. Choose Port and click Next.

5. Select TCP, and under Specific local ports, type: 5000 and click Next.

6. Choose Allow the connection and click Next.

7. Keep all three profile checkboxes ticked (Domain, Private, Public) and click Next.

8. Give it a name like Python KML Server and click Finish.