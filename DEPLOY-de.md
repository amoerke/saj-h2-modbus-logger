# Deployment auf dem Raspberry Pi

Ziel: `saj_logger.py` laeuft als systemd-Dienst unter `/opt/saj-logger`, startet
nach jedem Reboot automatisch und schreibt jede Minute nach Supabase.

Platzhalter in diesem Dokument: `<PI-IP>` (z. B. 192.168.1.50), `<USER>` (der
Login-Benutzer auf dem Pi, oft `pi`), `<WR-IP>` (IP des Wechselrichters bzw.
seines Kommunikationsmoduls), `<WR-MAC>` (dessen MAC-Adresse).

---

## 0. Vorbedingungen pruefen

1. **Supabase-Tabelle existiert.** Falls noch nicht: `schema.sql` im SQL-Editor
   von Supabase Studio ausfuehren. Gegenprobe im SQL-Editor:

   ```sql
   select count(*) from solar_readings;
   ```

2. **Kein zweiter Abfrager.** Wenn der Logger bisher auf dem Mac lief, dort
   beenden. Das Kommunikationsmodul des Wechselrichters vertraegt nur einen
   Client; parallele Abfragen koennen zur IP-Sperre fuehren.

3. **Pi haengt im richtigen Netz** (im selben Subnetz wie der Wechselrichter,
   nicht hinter einem weiteren Router/Repeater mit eigenem Netz). Vom Pi aus:

   ```bash
   nc -zv <WR-IP> 502
   ```

   Muss `succeeded!` melden. Tut es das nicht, ist alles Weitere zwecklos.

---

## 1. Pi vorbereiten

Per SSH einloggen:

```bash
ssh <USER>@<PI-IP>
```

Auf dem Pi:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip netcat-openbsd rsync
python3 --version          # 3.9+ reicht, 3.11 ist auf Bookworm Standard
```

---

## 2. Code auf den Pi kopieren

Vom Mac aus, aus dem Projektordner heraus. `venv`, `__pycache__`, Dumps und die
`.env` bleiben bewusst aussen vor — die `.env` kommt im naechsten Schritt
separat und mit engen Rechten:

```bash
cd <Projektordner>

rsync -av --exclude venv --exclude __pycache__ --exclude '.env' \
      --exclude '.DS_Store' --exclude 'neu' --exclude '*.csv' \
      ./ <USER>@<PI-IP>:/tmp/saj-logger/
```

Auf dem Pi an den Zielort verschieben:

```bash
sudo mkdir -p /opt
sudo mv /tmp/saj-logger /opt/saj-logger
sudo chown -R $USER:$USER /opt/saj-logger
```

---

## 3. Konfiguration (.env)

Die `.env` enthaelt den `service_role`-Key, der RLS umgeht. Sie gehoert nur auf
den Pi und muss `600` sein.

Vom Mac aus kopieren:

```bash
scp .env <USER>@<PI-IP>:/tmp/saj.env
```

Auf dem Pi:

```bash
mv /tmp/saj.env /opt/saj-logger/.env
chmod 600 /opt/saj-logger/.env
```

Kurz gegenlesen (`INVERTER_HOST`, `SUPABASE_URL`, `SUPABASE_TABLE`):

```bash
grep -v KEY /opt/saj-logger/.env
```

---

## 4. Virtualenv und Abhaengigkeiten

```bash
cd /opt/saj-logger
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

---

## 5. Manuell testen (vor dem Dienst!)

```bash
cd /opt/saj-logger
venv/bin/python saj_logger.py
```

Nach spaetestens ein paar Sekunden erscheint eine Zeile wie:

```
2026-09-15 20:41:03 INFO PV 3867 W | Batterie 1200 W (laedt), 76.0 % | Netz 33 W | Haus 2700 W
```

Pruefen:

- Plausible Werte im Vergleich zur Ampere-Home-App, besonders die Vorzeichen.
- Keine `Supabase 4xx`-Fehlermeldung — die kaeme sofort beim ersten Push.

Gegenprobe in Supabase:

```sql
select ts, pv_power, soc from solar_readings order by ts desc limit 5;
```

Dann mit `Strg+C` beenden. Falls eine `buffer.jsonl` entstanden ist, hat der
Push nicht geklappt — erst das Problem loesen, sonst laeuft der Dienst
dauerhaft ins Leere.

---

## 6. Als systemd-Dienst einrichten

Falls dein Benutzer nicht `pi` heisst, die Unit anpassen:

```bash
sed -i "s/^User=.*/User=$USER/" /opt/saj-logger/saj-logger.service
```

Installieren und starten:

```bash
sudo cp /opt/saj-logger/saj-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now saj-logger
```

Status und Live-Log:

```bash
systemctl status saj-logger
journalctl -u saj-logger -f
```

`enable` sorgt fuer den Autostart nach Reboot, `Restart=always` faengt Abstuerze
ab (30 s Pause). Beides einmal testen:

```bash
sudo reboot
# nach ~1 Minute wieder einloggen
systemctl is-active saj-logger      # -> active
```

---

## 7. Abnahme nach dem ersten Tag

```sql
-- Sollte ~60 pro Stunde sein
select date_trunc('hour', ts) as stunde, count(*)
from solar_readings
where ts > now() - interval '24 hours'
group by 1 order by 1 desc;
```

Luecken deuten auf Modbus-Lesefehler hin — diese stehen als
`Lesefehler (n in Folge)` im Journal:

```bash
journalctl -u saj-logger --since "24 hours ago" | grep -i fehler
```

---

## 8. Betrieb

**Journal-Groesse begrenzen** (sonst waechst das Log auf der SD-Karte):

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/size.conf
sudo systemctl restart systemd-journald
```

**Feste IP fuer den Wechselrichter** im Router reservieren: MAC
`<WR-MAC>` -> `<WR-IP>`. Ebenso fuer den Pi selbst, damit du ihn
wiederfindest.

**Update einspielen** (vom Mac aus):

```bash
rsync -av --exclude venv --exclude __pycache__ --exclude '.env' \
      --exclude '.DS_Store' --exclude 'neu' --exclude '*.csv' \
      ./ <USER>@<PI-IP>:/opt/saj-logger/
ssh <USER>@<PI-IP> 'sudo systemctl restart saj-logger'
```

Bei geaenderten Abhaengigkeiten zusaetzlich
`venv/bin/pip install -r requirements.txt`, bei geaenderter Unit-Datei erneut
kopieren und `sudo systemctl daemon-reload`.

**Nuetzliche Kommandos**

| Zweck | Kommando |
|---|---|
| Stoppen | `sudo systemctl stop saj-logger` |
| Starten | `sudo systemctl start saj-logger` |
| Autostart aus | `sudo systemctl disable saj-logger` |
| Log der letzten Stunde | `journalctl -u saj-logger --since -1h` |
| Puffer pruefen | `wc -l /opt/saj-logger/buffer.jsonl` |

---

## Fehlerbilder

| Symptom | Ursache / Abhilfe |
|---|---|
| `Keine Verbindung zu <WR-IP>:502` | Pi im falschen Netz oder IP gewechselt; `nc -zv` testen, IP im Router reservieren |
| Dauerhaft Lesefehler, vorher lief es | Modul hat die IP gesperrt, weil ein zweiter Client abfragt (Home Assistant, Mac-Skript). Alles andere abschalten, Wechselrichter-WLAN-Modul kurz stromlos machen |
| `Supabase 401` | Falscher Key in `.env` — es muss der `service_role`-Key sein, nicht `anon` |
| `Supabase 404` | Tabelle fehlt oder `SUPABASE_TABLE` falsch — `schema.sql` ausfuehren |
| `buffer.jsonl` waechst | VPS/Supabase nicht erreichbar; Daten gehen nicht verloren, werden beim naechsten erfolgreichen Push nachgeliefert (max. ~2 Wochen) |
| Dienst startet nach Reboot nicht | `systemctl is-enabled saj-logger` pruefen, ggf. `sudo systemctl enable saj-logger` |
