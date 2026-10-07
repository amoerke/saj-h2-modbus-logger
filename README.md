# SAJ/Ampere Logger: Inverter → Supabase

Reads the Ampere.StoragePro once per minute via Modbus TCP (read-only) and writes the values to Supabase. If the connection to the VPS is lost, the script buffers locally in `buffer.jsonl` and delivers later.

## 1. Create table in Supabase

Open Supabase Studio, go to the SQL Editor and run `schema.sql`. For the logger you need the API URL of your instance and the `service_role` key. In Coolify you'll find it in the environment variables of the Supabase service (usually `SERVICE_SUPABASESERVICE_KEY`). The key bypasses RLS, so it belongs only on the Pi and nowhere else.

## 2. Prepare Raspberry Pi

The Pi must be on the same subnet as the inverter, not behind another router or repeater with its own network. Test (`<INVERTER-IP>` = IP of the inverter):

```bash
nc -zv <INVERTER-IP> 502
```

## 3. Install

Copy from Mac (adjust Pi IP and user):

```bash
scp -r saj-logger pi@<PI-IP>:/tmp/
```

On the Pi:

```bash
sudo mv /tmp/saj-logger /opt/saj-logger
sudo chown -R pi:pi /opt/saj-logger
cd /opt/saj-logger
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env          # enter URL and key
chmod 600 .env
```

## 4. Test manually

```bash
venv/bin/python saj_logger.py
```

After the first run, a line appears like `PV 3867 W | Batterie ... | Netz ... | Haus ...` (the log output is in German). Compare the values with the Ampere Home App, especially the signs (see below). Exit with Ctrl+C.

## 5. Set up as a service

If your user is not named `pi`, adjust the `User=` line in `saj-logger.service`.

```bash
sudo cp saj-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now saj-logger
journalctl -u saj-logger -f     # live log
```

## Before going live, check

- **Only one querier:** Don't run Home Assistant or other tools in parallel against the inverter. The WiFi/communications module can block the IP with too many requests. That's why the script enforces a minimum 60 s interval.
- **Reserve IP:** Assign the inverter's IP (`<INVERTER-IP>`) to its MAC address permanently in the router.

## What is verified

Calibrated against the daily kWh counters (as of 2026-09-26, 8300 measurements over six days, correlation and scaling factor per register):

| Quantity | Offset | r | Factor |
|---|---|---|---|
| `load_power` house consumption | 11 | +0.98 | 0.99 |
| `battery_power` | 17 | +0.98 | 1.01 |
| `pv_power` total generation | 16 | +0.99 | 1.00 |

- `battery_power`: **positive = discharging, negative = charging**. Verified on 2026-09-14 evening at 76% SoC with no generation. The claim in the integration's wiki (positive = charging) is wrong on this point.
- `grid_import_today_kwh` / `grid_export_today_kwh`: The SAJ registers are called "feedin" and "sell", but mean the opposite of what the names suggest. At night with 0 W PV, only "feedin" rose — so that is **import**. The columns have been renamed accordingly.

The four daily counters `pv_today_kwh`, `load_today_kwh`, `grid_import_today_kwh`, and `grid_export_today_kwh` are thus reliable. They are daily counters in kWh and reset at midnight, so the daily value is the maximum:

```sql
select (ts at time zone 'Europe/Berlin')::date as day,
       max(pv_today_kwh)          as pv_kwh,
       max(grid_export_today_kwh) as export_kwh,
       max(grid_import_today_kwh) as import_kwh,
       max(load_today_kwh)        as house_kwh
from solar_readings group by 1 order by 1 desc;
```

## There is no third PV string

The inverter has two inputs. In the string block 0x406E, at offsets 9 and 10, where voltage and current of a third input would be, 0xFFFF appears permanently — the identifier for "not used". Offset 11 remains 0. The sum of string 1 and string 2 equals `pv_power`.

This also refutes the earlier hypothesis of **offset 14 as a third string**: The register correlates with r = +0.97 to PV power, but only amounts to about one-third of it, while offset 16 already fully captures total generation. What it exactly is remains open; it runs as a column `offset14_power`.

## Open: the instantaneous value for the grid

`grid_power` (offset 24) is **not** the grid connection point. In the register mapping against the counters, no offset of block 0x4095 for the grid achieves more than r = 0.20; offset 24 doesn't even appear in the top four. Offset 12 delivers the same value as 24, so it's a duplicate — likewise, offsets 19 and 21 are an identical pair that consistently sits above `load_power` and is therefore more likely apparent power in VA.

For daily balances, this is irrelevant; the kWh registers matter there. Whoever needs the instantaneous value calculates it from the balance:

    grid = load_power - pv_power - max(0, battery_power) + max(0, -battery_power)

For further investigation, the raw block 0x4095 is stored in the column `raw_power_block`.

## Data volume

1 measurement per minute yields around 525,000 rows per year, so only a few hundred MB. For long-term evaluations, a daily aggregate table filled by n8n at night will be worthwhile later.

## Connection test from Mac

Before the Pi comes into play, you can check everything from the Mac. In the unpacked folder:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env    # and fill in
venv/bin/python test_mac.py
```

The script checks the Modbus connection in sequence (including serial number), whether Supabase is reachable and the key is correct, and then writes a real measurement value as a row to the table. With `--dry-run`, nothing is written.
