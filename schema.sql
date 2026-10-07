-- ACHTUNG: loescht die bestehende Tabelle inklusive aller Messwerte!
drop table if exists public.solar_readings cascade;

-- Tabelle für die Messwerte des Wechselrichters
create table if not exists public.solar_readings (
  id                      bigint generated always as identity primary key,
  ts                      timestamptz not null unique,
  -- Systemwerte wie in der Ampere-App (Block 0x4200, ab 06.10.2026)
  sys_pv_power            integer,   -- 0x4204 PV gesamt inkl. SolarMax
  sys_battery_power       integer,   -- 0x4205 positiv = entladen
  sys_grid_power          integer,   -- 0x4206 positiv = Bezug, negativ = Einspeisung
  sys_house_power         integer,   -- 0x4207 Hausverbrauch
  sys_soc                 smallint,  -- 0x4209 Ladezustand in %
  -- Momentanwerte (W)
  pv_power                integer,   -- Summe String 1 + 2
  raw_power_block         jsonb,     -- Rohblock 0x4095, zur Klaerung offener Register
  raw_string_block        jsonb,     -- Rohblock 0x406E, zur Klaerung des dritten Strings
  offset14_power          integer,   -- Offset 14: korreliert mit PV, Deutung offen
  battery_power           integer,   -- positiv = entladen, negativ = laden
  grid_power              integer,   -- Richtung noch ungeklaert
  total_grid_power        integer,
  load_power              integer,
  inverter_power          integer,
  direction_pv            smallint,
  direction_battery       smallint,
  direction_grid          smallint,
  -- Batterie
  soc                     numeric(5,2),
  bat_temp                numeric(4,1),
  -- PV-Strings
  pv1_voltage             numeric(5,1),
  pv1_current             numeric(6,2),
  pv1_power               integer,
  pv2_voltage             numeric(5,1),
  pv2_current             numeric(6,2),
  pv2_power               integer,
  pv3_voltage             numeric(5,1),   -- dritter Eingang, falls vorhanden
  pv3_current             numeric(6,2),
  pv3_power               integer,
  -- Energie (kWh)
  pv_today_kwh            numeric(10,2),
  pv_total_kwh            numeric(12,2),
  bat_charge_today_kwh    numeric(10,2),
  bat_discharge_today_kwh numeric(10,2),
  load_today_kwh          numeric(10,2),
  grid_export_today_kwh   numeric(10,2),     -- SAJ-Register "sell"
  grid_import_today_kwh   numeric(10,2)      -- SAJ-Register "feedin"
);

create index if not exists solar_readings_ts_idx on public.solar_readings (ts desc);

-- RLS an: ohne Policies kommt nur der service_role-Key an die Daten
alter table public.solar_readings enable row level security;

-- Falls die Tabelle schon mit den alten Spaltennamen existiert:
-- alter table public.solar_readings rename column sell_today_kwh to grid_export_today_kwh;
-- alter table public.solar_readings rename column feedin_today_kwh to grid_import_today_kwh;

-- Falls die Tabelle schon ohne die neuen Spalten existiert:
-- alter table public.solar_readings add column if not exists pv3_power integer;
-- alter table public.solar_readings add column if not exists raw_power_block jsonb;
-- Fuer eine bestehende Tabelle: migration_2026-09-22.sql und migration_2026-10-06.sql ausfuehren.
