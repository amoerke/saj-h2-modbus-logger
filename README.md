# SAJ/Ampere Logger: Wechselrichter → Supabase

Liest den Ampere.StoragePro einmal pro Minute per Modbus TCP aus (nur lesend) und schreibt die Werte in Supabase. Fällt die Verbindung zum VPS aus, puffert das Skript lokal in `buffer.jsonl` und liefert später nach.

## 1. Tabelle in Supabase anlegen

Öffne Supabase Studio, geh in den SQL-Editor und führe `schema.sql` aus. Für den Logger brauchst du die API-URL deiner Instanz und den `service_role`-Key. Bei Coolify findest du ihn in den Umgebungsvariablen des Supabase-Services (meist `SERVICE_SUPABASESERVICE_KEY`). Der Key umgeht RLS, darum gehört er nur auf den Pi und nirgendwo sonst hin.

## 2. Raspberry Pi vorbereiten

Der Pi muss im selben Subnetz wie der Wechselrichter hängen, nicht hinter einem weiteren Router oder Repeater mit eigenem Netz. Test (`<WR-IP>` = IP des Wechselrichters):

```bash
nc -zv <WR-IP> 502
```

## 3. Installieren

Vom Mac aus kopieren (Pi-IP und Benutzer anpassen):

```bash
scp -r saj-logger pi@<PI-IP>:/tmp/
```

Auf dem Pi:

```bash
sudo mv /tmp/saj-logger /opt/saj-logger
sudo chown -R pi:pi /opt/saj-logger
cd /opt/saj-logger
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env          # URL und Key eintragen
chmod 600 .env
```

## 4. Manuell testen

```bash
venv/bin/python saj_logger.py
```

Nach dem ersten Durchlauf erscheint eine Zeile wie `PV 3867 W | Batterie ... | Netz ... | Haus ...`. Vergleich die Werte mit der Ampere Home App, vor allem die Vorzeichen (siehe unten). Mit Strg+C beenden.

## 5. Als Dienst einrichten

Falls dein Benutzer nicht `pi` heißt, in `saj-logger.service` die Zeile `User=` anpassen.

```bash
sudo cp saj-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now saj-logger
journalctl -u saj-logger -f     # Live-Log
```

## Vor dem Produktivbetrieb prüfen

- **Nur ein Abfrager:** Nicht parallel Home Assistant oder andere Tools auf den Wechselrichter loslassen. Das WLAN-/Kommunikationsmodul kann bei zu vielen Anfragen die IP sperren. Deshalb erzwingt das Skript mindestens 60 s Intervall.
- **IP reservieren:** Die IP des Wechselrichters (`<WR-IP>`) im Router fest seiner MAC-Adresse zuordnen.

## Was gesichert ist

Gegen die kWh-Tageszaehler kalibriert (Stand 26.09.2026, 8300 Messungen ueber
sechs Tage, Korrelation und Skalierungsfaktor je Register):

| Groesse | Offset | r | Faktor |
|---|---|---|---|
| `load_power` Hausverbrauch | 11 | +0.98 | 0.99 |
| `battery_power` | 17 | +0.98 | 1.01 |
| `pv_power` Gesamterzeugung | 16 | +0.99 | 1.00 |

- `battery_power`: **positiv = entladen, negativ = laden**. Am 14.09.2026 abends
  bei 76 % SoC ohne Erzeugung verifiziert. Die Angabe im Wiki der Integration
  (positiv = laden) ist an dieser Stelle falsch.
- `grid_import_today_kwh` / `grid_export_today_kwh`: Die SAJ-Register heissen
  "feedin" und "sell", meinen aber das Gegenteil dessen, was der Name nahelegt.
  Nachts bei 0 W PV stieg ausschliesslich "feedin" -- das ist also der **Bezug**.
  Die Spalten sind hier entsprechend umbenannt.

Die vier Tageszaehler `pv_today_kwh`, `load_today_kwh`, `grid_import_today_kwh`
und `grid_export_today_kwh` sind damit belastbar. Sie sind Tageszaehler in kWh
und werden um Mitternacht zurueckgesetzt, der Tageswert ist also das Maximum:

```sql
select (ts at time zone 'Europe/Berlin')::date as tag,
       max(pv_today_kwh)          as pv_kwh,
       max(grid_export_today_kwh) as einspeisung_kwh,
       max(grid_import_today_kwh) as bezug_kwh,
       max(load_today_kwh)        as haus_kwh
from solar_readings group by 1 order by 1 desc;
```

## Es gibt keinen dritten PV-String

Der Wechselrichter hat zwei Eingaenge. Im String-Block 0x406E stehen auf den
Offsets 9 und 10, wo Spannung und Strom eines dritten Eingangs liegen wuerden,
dauerhaft 0xFFFF -- die Kennung fuer "nicht belegt". Offset 11 bleibt 0. Die
Summe von String 1 und String 2 entspricht `pv_power`.

Damit ist auch die frueher vermutete Deutung von **Offset 14 als dritter String
widerlegt**: Das Register korreliert mit r = +0.97 zur PV-Leistung, betraegt
aber nur rund ein Drittel davon, waehrend Offset 16 die Gesamterzeugung schon
vollstaendig abbildet. Was es genau ist, ist offen; es laeuft als Spalte
`offset14_power` mit.

## Offen: der Momentanwert fuer das Netz

`grid_power` (Offset 24) ist **nicht** der Netzanschlusspunkt. In der
Registerzuordnung gegen die Zaehler erreicht kein Offset des Blocks 0x4095 fuer
das Netz mehr als r = 0.20; Offset 24 erscheint nicht einmal unter den besten
vier. Offset 12 liefert denselben Wert wie 24, ist also eine Dublette -- ebenso
sind Offset 19 und 21 ein identisches Paar, das durchgaengig ueber
`load_power` liegt und daher eher Scheinleistung in VA sein duerfte.

Fuer Tagesbilanzen ist das ohne Belang, dort zaehlen die kWh-Register. Wer den
Momentanwert braucht, rechnet ihn aus der Bilanz:

    Netz = Haus - pv_power - max(0, battery_power) + max(0, -battery_power)

Zur weiteren Suche laeuft der Rohblock 0x4095 als Spalte `raw_power_block` mit.
Auswertung:

```bash
venv/bin/python analyse_bilanz.py --limit 20000
```

Das Skript kalibriert jedes Register des Blocks gegen die kWh-Zaehler (Block 6)
und stellt die Energiebilanz ueber den gesamten Zeitraum auf (Block 8).
`analyse_register.py` ist der aeltere, groebere Vorlaeufer; seine Nacht-Tests
melden schon bei 18 W "keine Erzeugung" und sind mit Vorsicht zu lesen.

## Datenmenge

1 Messung pro Minute ergibt rund 525.000 Zeilen pro Jahr, also nur einige Hundert MB. Für Langzeitauswertungen lohnt sich später eine Tagesaggregat-Tabelle, die z. B. n8n nachts befüllt.

## Verbindungstest vom Mac

Bevor der Pi ins Spiel kommt, kannst du alles vom Mac aus prüfen. Im entpackten Ordner:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env    # und ausfüllen
venv/bin/python test_mac.py
```

Das Skript prüft nacheinander die Modbus-Verbindung (inkl. Seriennummer), ob Supabase erreichbar ist und der Key passt, und schreibt dann einen echten Messwert als Zeile in die Tabelle. Mit `--dry-run` wird nichts geschrieben.
