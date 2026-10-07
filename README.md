# SAJ/Ampere Logger: Inverter → Supabase

Reads the Ampere.StoragePro once per minute via Modbus TCP (read-only) and writes the values to Supabase. If the connection to the VPS is lost, the script buffers locally in `buffer.jsonl` and delivers later.

The Ampere.StoragePro E2 (model ASP 10KW-3P-X) is a rebranded **SAJ H2/HS2** hybrid inverter, so the SAJ H2 register map applies. Ampere publishes no register documentation; the addresses come from the Home Assistant integrations [stanus74/home-assistant-saj-h2-modbus](https://github.com/stanus74/home-assistant-saj-h2-modbus) and [dboeni/home-assistant-ampere-storage-pro-modbus](https://github.com/dboeni/home-assistant-ampere-storage-pro-modbus) and were verified against this installation (see below). Unit ID 1 works here.

A second PV inverter, a **SolarMax 4600SP**, feeds into the house grid separately. The StoragePro only sees it through a current clamp (CT).

## 1. Create table in Supabase

Open Supabase Studio, go to the SQL Editor and run `schema.sql`. Caution: `schema.sql` drops an existing table including all readings. To update an existing table, run only the commented `alter table … add column if not exists` lines at the end of `schema.sql` instead; they keep all data. **Add new columns before restarting the logger with new code**, otherwise Supabase rejects the rows (they stay in `buffer.jsonl` and are delivered once the columns exist). For the logger you need the API URL of your instance and the `service_role` key. In Coolify you'll find it in the environment variables of the Supabase service (usually `SERVICE_SUPABASESERVICE_KEY`). The key bypasses RLS, so it belongs only on the Pi and nowhere else.

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

After the first run, a line appears like `PV 1878 W | Batterie 1316 W (laedt), 34 % | Netz 24 W (Einspeisung) | Haus 537 W` (the log output is in German). These are the `sys_*` values from block 0x4200 and should match the Ampere Home App. Exit with Ctrl+C.

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

## Live values: block 0x4200 (matches the app)

Since 2026-10-06 the logger also reads the registers 0x4204–0x4209. They contain exactly the values the Ampere Home App shows, including the SolarMax, and the power balance adds up. Verified on 2026-10-06 at 09:21 against the app (PV 1.9 kW, battery charging 1.3 kW, grid 0.0 kW, house 0.5 kW, SoC 34 %); the balance matches to the watt: 1878 W PV = 1316 W battery + 537 W house + 24 W export.

| Register | Column | Meaning | Sign |
|---|---|---|---|
| 0x4204 | `sys_pv_power` | total PV **including SolarMax** (= 0x40A5 + 0x40A3) | a few W negative at night (standby) |
| 0x4205 | `sys_battery_power` | battery | positive = discharging, negative = charging |
| 0x4206 | `sys_grid_power` | grid connection point | positive = import, negative = export |
| 0x4207 | `sys_house_power` | house consumption | – |
| 0x4208 | – | unknown, always 0 so far | – |
| 0x4209 | `sys_soc` | state of charge in % | – |

The signs already follow the convention of the existing columns, nothing is converted. Export = negative is verified directly; import = positive follows from the same signed register and the balance, and is checked with the first night values:

```sql
select ts at time zone 'Europe/Berlin' as time,
       sys_pv_power, sys_battery_power, sys_grid_power, sys_house_power,
       sys_pv_power + sys_battery_power + sys_grid_power - sys_house_power as balance_rest
from solar_readings
where sys_grid_power > 50
order by ts desc limit 10;
```

`balance_rest` should be close to 0. Note: the SAJ issue [#171](https://github.com/stanus74/home-assistant-saj-h2-modbus/issues/171) lists this block in a different order (0x4204 = grid, 0x4206 = PV). On this installation the table above applies.

**The dashboard uses the `sys_*` columns.** All other columns keep being logged for detailed analysis (per string, energy counters, raw blocks).

## What is verified

Calibrated against the daily kWh counters (as of 2026-09-26, 8300 measurements over six days, correlation and scaling factor per register):

| Quantity | Offset | r | Factor |
|---|---|---|---|
| `load_power` house consumption (0x40A0) | 11 | +0.98 | 0.99 |
| `battery_power` | 17 | +0.98 | 1.01 |
| `pv_power` total generation | 16 | +0.99 | 1.00 |

- `load_power` (0x40A0) correlates well with the daily counter, but the **instantaneous value is too high**: on average about 190 W in September, often more than 300 W in the evening while the battery discharges (2026-10-05 19:10: register 695 W, app 0.4 kW). In all 8,446 measurements from Sept 15–21, `load_power = |offset 12 + offset 18|` exactly. Don't use it for live values; use `sys_house_power`.
- `battery_power`: **positive = discharging, negative = charging**. Verified on 2026-09-14 evening at 76% SoC with no generation. The claim in the integration's wiki (positive = charging) is wrong on this point.
- `grid_import_today_kwh` / `grid_export_today_kwh`: The SAJ registers are called "feedin" and "sell", but mean the opposite of what the names suggest. At night with 0 W PV, only "feedin" rose — so that is **import**. The columns have been renamed accordingly.

The four daily counters `pv_today_kwh`, `load_today_kwh`, `grid_import_today_kwh`, and `grid_export_today_kwh` are thus reliable. Caveat: `pv_today_kwh` counts only the two strings on the StoragePro, most likely **without the SolarMax**. There is no known kWh counter for the SolarMax; its share has to be integrated from `sys_pv_power - pv_power` (or `offset14_power`) or read from the SolarMax itself. They are daily counters in kWh and reset at midnight, so the daily value is the maximum:

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

This also refutes the earlier hypothesis of **offset 14 as a third string**.

## Offset 14 is the SolarMax

Offset 14 of block 0x4095 is register **0x40A3, "CT PV Power"** in the SAJ map: the output of an external PV inverter, measured with a current clamp. Here that is the SolarMax 4600SP. Confirmed on 2026-10-06: `pv_power` 1584 W + `offset14_power` 318 W = 1902 W, the app showed 1.9 kW. It is 0 at night and typically about 20–30 % of total generation. The column keeps its name `offset14_power`.

Other offsets of block 0x4095 by SAJ name: 12 = CT grid power (0x40A1), 18 = total grid power (0x40A7), 19/21 = apparent power in VA (0x40A8 / 0x40AA), 20 = inverter power (0x40A9), 24 = grid load power (0x40AD).

## Solved: the instantaneous value for the grid

Use `sys_grid_power` (0x4206, see above). History of the search: `grid_power` (offset 24) is **not** the grid connection point. In the register mapping against the counters, no offset of block 0x4095 for the grid achieves more than r = 0.20; offset 24 doesn't even appear in the top four. Offset 12 delivers the same value as 24, so it's a duplicate — likewise, offsets 19 and 21 are an identical pair that consistently sits above `load_power` and is therefore more likely apparent power in VA.

For rows before 2026-10-06 (without `sys_*`), the dashboard falls back to `pv_power + offset14_power` for PV and computes the house consumption from the balance `PV + battery_power + grid_power`; `load_power` is not used. The raw block 0x4095 is still stored in `raw_power_block`.

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
