# Deployment on the Raspberry Pi

Goal: `saj_logger.py` runs as a systemd service from `/opt/saj-logger`, starts
automatically after every reboot and writes to Supabase once a minute.

Placeholders in this document: `<PI-IP>` (e.g. 192.168.1.50), `<USER>` (the
login user on the Pi, often `pi`), `<INVERTER-IP>` (IP of the inverter or its
communication module), `<INVERTER-MAC>` (its MAC address).

Note: the logger's log output is in German. Messages quoted below are shown
verbatim so you can search for them.

---

## 0. Check prerequisites

1. **The Supabase table exists.** If not yet: run `schema.sql` in the SQL editor
   of Supabase Studio. Cross-check in the SQL editor:

   ```sql
   select count(*) from solar_readings;
   ```

2. **No second client.** If the logger was previously running on your Mac, stop
   it there. The inverter's communication module tolerates only one client;
   parallel queries can get the IP blocked.

3. **The Pi is on the right network** (the same subnet as the inverter, not
   behind another router or repeater with its own network). From the Pi:

   ```bash
   nc -zv <INVERTER-IP> 502
   ```

   It must report `succeeded!`. If it doesn't, nothing else will work.

---

## 1. Prepare the Pi

Log in via SSH:

```bash
ssh <USER>@<PI-IP>
```

On the Pi:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip netcat-openbsd rsync
python3 --version          # 3.9+ is enough, 3.11 is the default on Bookworm
```

---

## 2. Copy the code to the Pi

From your Mac, inside the project folder. `venv`, `__pycache__`, dumps and the
`.env` are deliberately excluded — the `.env` is copied separately in the next
step, with tight permissions:

```bash
cd <project-folder>

rsync -av --exclude venv --exclude __pycache__ --exclude '.env' \
      --exclude '.DS_Store' --exclude 'neu' --exclude '*.csv' \
      ./ <USER>@<PI-IP>:/tmp/saj-logger/
```

On the Pi, move it into place:

```bash
sudo mkdir -p /opt
sudo mv /tmp/saj-logger /opt/saj-logger
sudo chown -R $USER:$USER /opt/saj-logger
```

---

## 3. Configuration (.env)

The `.env` contains the `service_role` key, which bypasses RLS. It belongs on
the Pi only and must be `600`.

Copy it from your Mac:

```bash
scp .env <USER>@<PI-IP>:/tmp/saj.env
```

On the Pi:

```bash
mv /tmp/saj.env /opt/saj-logger/.env
chmod 600 /opt/saj-logger/.env
```

Quick sanity check (`INVERTER_HOST`, `SUPABASE_URL`, `SUPABASE_TABLE`):

```bash
grep -v KEY /opt/saj-logger/.env
```

---

## 4. Virtualenv and dependencies

```bash
cd /opt/saj-logger
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

---

## 5. Test manually (before setting up the service!)

```bash
cd /opt/saj-logger
venv/bin/python saj_logger.py
```

After a few seconds at most, a line like this appears:

```
2026-09-15 20:41:03 INFO PV 3867 W | Batterie 1200 W (laedt), 76.0 % | Netz 33 W | Haus 2700 W
```

Check:

- The values are plausible compared to the Ampere Home App, especially the signs.
- No `Supabase 4xx` error message — it would appear right at the first push.

Cross-check in Supabase:

```sql
select ts, pv_power, soc from solar_readings order by ts desc limit 5;
```

Then stop with `Ctrl+C`. If a `buffer.jsonl` has been created, the push did not
work — fix that first, otherwise the service will keep running into the void.

---

## 6. Set up as a systemd service

If your user is not called `pi`, adjust the unit:

```bash
sed -i "s/^User=.*/User=$USER/" /opt/saj-logger/saj-logger.service
```

Install and start:

```bash
sudo cp /opt/saj-logger/saj-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now saj-logger
```

Status and live log:

```bash
systemctl status saj-logger
journalctl -u saj-logger -f
```

`enable` takes care of autostart after a reboot, `Restart=always` catches
crashes (30 s pause). Test both once:

```bash
sudo reboot
# log in again after ~1 minute
systemctl is-active saj-logger      # -> active
```

---

## 7. Acceptance check after the first day

```sql
-- Should be ~60 per hour
select date_trunc('hour', ts) as hour, count(*)
from solar_readings
where ts > now() - interval '24 hours'
group by 1 order by 1 desc;
```

Gaps point to Modbus read errors — they show up in the journal as
`Lesefehler (n in Folge)` ("read error, n in a row"):

```bash
journalctl -u saj-logger --since "24 hours ago" | grep -i fehler
```

---

## 8. Operation

**Limit the journal size** (otherwise the log keeps growing on the SD card):

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/size.conf
sudo systemctl restart systemd-journald
```

**Fixed IP for the inverter:** reserve it in your router: MAC
`<INVERTER-MAC>` -> `<INVERTER-IP>`. Do the same for the Pi itself so you can
always find it.

**Deploy an update** (from your Mac):

```bash
rsync -av --exclude venv --exclude __pycache__ --exclude '.env' \
      --exclude '.DS_Store' --exclude 'neu' --exclude '*.csv' \
      ./ <USER>@<PI-IP>:/opt/saj-logger/
ssh <USER>@<PI-IP> 'sudo systemctl restart saj-logger'
```

If dependencies changed, also run `venv/bin/pip install -r requirements.txt`;
if the unit file changed, copy it again and run `sudo systemctl daemon-reload`.

**Useful commands**

| Purpose | Command |
|---|---|
| Stop | `sudo systemctl stop saj-logger` |
| Start | `sudo systemctl start saj-logger` |
| Disable autostart | `sudo systemctl disable saj-logger` |
| Log of the last hour | `journalctl -u saj-logger --since -1h` |
| Check the buffer | `wc -l /opt/saj-logger/buffer.jsonl` |

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Keine Verbindung zu <INVERTER-IP>:502` ("no connection") | Pi on the wrong network or the IP changed; test with `nc -zv`, reserve the IP in the router |
| Persistent read errors, although it worked before | The module has blocked the IP because a second client is querying (Home Assistant, script on the Mac). Switch everything else off, briefly cut power to the inverter's WiFi module |
| `Supabase 401` | Wrong key in `.env` — it must be the `service_role` key, not `anon` |
| `Supabase 404` | Table missing or `SUPABASE_TABLE` wrong — run `schema.sql` |
| `buffer.jsonl` keeps growing | VPS/Supabase unreachable; no data is lost, it is delivered with the next successful push (max. ~2 weeks) |
| Service doesn't start after reboot | Check `systemctl is-enabled saj-logger`, if needed `sudo systemctl enable saj-logger` |
