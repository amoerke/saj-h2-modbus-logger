# SAJ/Ampere Logger: Wechselrichter → Supabase

Liest den Ampere.StoragePro einmal pro Minute per Modbus TCP aus (nur lesend) und schreibt die Werte in Supabase. Fällt die Verbindung zum VPS aus, puffert das Skript lokal in `buffer.jsonl` und liefert später nach.

Der Ampere.StoragePro E2 (Modell ASP 10KW-3P-X) ist ein umgelabelter **SAJ H2/HS2**-Hybridwechselrichter, es gilt also die SAJ-H2-Registerliste. Ampere veröffentlicht keine Registerdoku; die Adressen stammen aus den Home-Assistant-Integrationen [stanus74/home-assistant-saj-h2-modbus](https://github.com/stanus74/home-assistant-saj-h2-modbus) und [dboeni/home-assistant-ampere-storage-pro-modbus](https://github.com/dboeni/home-assistant-ampere-storage-pro-modbus) und sind an dieser Anlage überprüft (siehe unten). Unit-ID 1 funktioniert hier.

Ein zweiter PV-Wechselrichter, ein **SolarMax 4600SP**, speist separat ins Hausnetz ein. Der StoragePro sieht ihn nur über einen Stromwandler (CT).

## 1. Tabelle in Supabase anlegen

Öffne Supabase Studio, geh in den SQL-Editor und führe `schema.sql` aus. Achtung: `schema.sql` löscht eine bestehende Tabelle samt allen Messwerten. Um eine bestehende Tabelle zu aktualisieren, führst du stattdessen nur die auskommentierten Zeilen `alter table … add column if not exists` am Ende von `schema.sql` aus; die Daten bleiben erhalten. **Neue Spalten vor dem Neustart des Loggers mit neuem Code anlegen**, sonst lehnt Supabase die Zeilen ab (sie bleiben in `buffer.jsonl` und werden nachgeliefert, sobald die Spalten existieren). Für den Logger brauchst du die API-URL deiner Instanz und den `service_role`-Key. Bei Coolify findest du ihn in den Umgebungsvariablen des Supabase-Services (meist `SERVICE_SUPABASESERVICE_KEY`). Der Key umgeht RLS, darum gehört er nur auf den Pi und nirgendwo sonst hin.

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

Nach dem ersten Durchlauf erscheint eine Zeile wie `PV 1878 W | Batterie 1316 W (laedt), 34 % | Netz 24 W (Einspeisung) | Haus 537 W`. Das sind die `sys_*`-Werte aus Block 0x4200; sie sollten mit der Ampere Home App übereinstimmen. Mit Strg+C beenden.

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

## Livewerte: Block 0x4200 (wie in der App)

Seit 06.10.2026 liest der Logger zusätzlich die Register 0x4204–0x4209. Sie enthalten genau die Werte, die die Ampere Home App anzeigt, inklusive SolarMax, und die Leistungsbilanz geht auf. Am 06.10.2026 um 09:21 Uhr gegen die App geprüft (PV 1,9 kW, Batterie lädt 1,3 kW, Netz 0,0 kW, Haus 0,5 kW, SoC 34 %); die Bilanz stimmt auf das Watt: 1878 W PV = 1316 W Batterie + 537 W Haus + 24 W Einspeisung.

| Register | Spalte | Bedeutung | Vorzeichen |
|---|---|---|---|
| 0x4204 | `sys_pv_power` | PV gesamt **inkl. SolarMax** (= 0x40A5 + 0x40A3) | nachts wenige W negativ (Standby) |
| 0x4205 | `sys_battery_power` | Batterie | positiv = entladen, negativ = laden |
| 0x4206 | `sys_grid_power` | Netzanschlusspunkt | positiv = Bezug, negativ = Einspeisung |
| 0x4207 | `sys_house_power` | Hausverbrauch | – |
| 0x4208 | – | unbekannt, bisher immer 0 | – |
| 0x4209 | `sys_soc` | Ladezustand in % | – |

Die Vorzeichen entsprechen bereits der Konvention der bestehenden Spalten, umgerechnet wird nichts. Einspeisung = negativ ist direkt geprüft; Bezug = positiv folgt aus demselben vorzeichenbehafteten Register und der Bilanz und wird mit den ersten Nachtwerten bestätigt:

```sql
select ts at time zone 'Europe/Berlin' as zeit,
       sys_pv_power, sys_battery_power, sys_grid_power, sys_house_power,
       sys_pv_power + sys_battery_power + sys_grid_power - sys_house_power as bilanz_rest
from solar_readings
where sys_grid_power > 50
order by ts desc limit 10;
```

`bilanz_rest` sollte nahe 0 liegen. Hinweis: Das SAJ-Issue [#171](https://github.com/stanus74/home-assistant-saj-h2-modbus/issues/171) nennt für diesen Block eine andere Reihenfolge (0x4204 = Netz, 0x4206 = PV). An dieser Anlage gilt die Tabelle oben.

**Das Dashboard nutzt die `sys_*`-Spalten.** Alle übrigen Spalten werden für Detailauswertungen weiter geloggt (je String, Energiezähler, Rohblöcke).

## Was gesichert ist

Gegen die kWh-Tageszaehler kalibriert (Stand 26.09.2026, 8300 Messungen ueber
sechs Tage, Korrelation und Skalierungsfaktor je Register):

| Groesse | Offset | r | Faktor |
|---|---|---|---|
| `load_power` Hausverbrauch (0x40A0) | 11 | +0.98 | 0.99 |
| `battery_power` | 17 | +0.98 | 1.01 |
| `pv_power` Gesamterzeugung | 16 | +0.99 | 1.00 |

- `load_power` (0x40A0) passt gut zum Tageszähler, der **Momentanwert ist aber zu hoch**: im September im Mittel um rund 190 W, abends beim Entladen der Batterie oft um mehr als 300 W (05.10.2026, 19:10 Uhr: Register 695 W, App 0,4 kW). In allen 8.446 Messungen vom 15.–21.09. gilt exakt `load_power = |Offset 12 + Offset 18|`. Für Livewerte nicht verwenden, stattdessen `sys_house_power`.
- `battery_power`: **positiv = entladen, negativ = laden**. Am 14.09.2026 abends
  bei 76 % SoC ohne Erzeugung verifiziert. Die Angabe im Wiki der Integration
  (positiv = laden) ist an dieser Stelle falsch.
- `grid_import_today_kwh` / `grid_export_today_kwh`: Die SAJ-Register heissen
  "feedin" und "sell", meinen aber das Gegenteil dessen, was der Name nahelegt.
  Nachts bei 0 W PV stieg ausschliesslich "feedin" -- das ist also der **Bezug**.
  Die Spalten sind hier entsprechend umbenannt.

Die vier Tageszaehler `pv_today_kwh`, `load_today_kwh`, `grid_import_today_kwh`
und `grid_export_today_kwh` sind damit belastbar. Einschränkung: `pv_today_kwh`
zählt nur die beiden Strings am StoragePro, sehr wahrscheinlich **ohne den
SolarMax**. Einen kWh-Zähler für den SolarMax kennen wir nicht; seinen Anteil
muss man aus `sys_pv_power - pv_power` (bzw. `offset14_power`) aufsummieren oder
am SolarMax selbst auslesen.

Die vier Werte sind Tageszaehler in kWh und werden um Mitternacht
zurueckgesetzt, der Tageswert ist also das Maximum:

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
widerlegt**.

## Offset 14 ist der SolarMax

Offset 14 des Blocks 0x4095 ist in der SAJ-Liste das Register **0x40A3, „CT PV
Power“**: die Leistung eines externen PV-Wechselrichters, gemessen über einen
Stromwandler. Hier ist das der SolarMax 4600SP. Am 06.10.2026 bestätigt:
`pv_power` 1584 W + `offset14_power` 318 W = 1902 W, die App zeigte 1,9 kW. Nachts
ist der Wert 0, tagsüber meist etwa 20–30 % der Gesamterzeugung. Die Spalte
behält ihren Namen `offset14_power`.

Weitere Offsets des Blocks 0x4095 nach SAJ-Namen: 12 = CT-Netzleistung (0x40A1),
18 = Total Grid Power (0x40A7), 19/21 = Scheinleistung in VA (0x40A8 / 0x40AA),
20 = Wechselrichterleistung (0x40A9), 24 = Grid Load Power (0x40AD).

## Gelöst: der Momentanwert fuer das Netz

`sys_grid_power` verwenden (0x4206, siehe oben). Zur Geschichte der Suche:
`grid_power` (Offset 24) ist **nicht** der Netzanschlusspunkt. In der
Registerzuordnung gegen die Zaehler erreicht kein Offset des Blocks 0x4095 fuer
das Netz mehr als r = 0.20; Offset 24 erscheint nicht einmal unter den besten
vier. Offset 12 liefert denselben Wert wie 24, ist also eine Dublette -- ebenso
sind Offset 19 und 21 ein identisches Paar, das durchgaengig ueber
`load_power` liegt und daher eher Scheinleistung in VA sein duerfte.

Für Zeilen vor dem 06.10.2026 (ohne `sys_*`) nimmt das Dashboard als Rückfall
`pv_power + offset14_power` für die PV und rechnet den Hausverbrauch aus der
Bilanz `PV + battery_power + grid_power`; `load_power` wird nicht verwendet. Der
Rohblock 0x4095 läuft weiter als Spalte `raw_power_block` mit. Auswertung:

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
