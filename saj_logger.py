#!/usr/bin/env python3
"""
SAJ / Ampere.StoragePro -> Supabase Logger

Liest den Wechselrichter per Modbus TCP aus (nur lesend!) und schreibt
die Werte in eine Supabase-Tabelle. Registeradressen stammen aus der
Home-Assistant-Integration stanus74/home-assistant-saj-h2-modbus.

Wenn Supabase nicht erreichbar ist, werden Messwerte lokal in
buffer.jsonl gepuffert und beim nächsten erfolgreichen Durchlauf
nachgeliefert.
"""
import json
import logging
import os
import signal
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from pymodbus.client import ModbusTcpClient

load_dotenv(Path(__file__).with_name(".env"))

INVERTER_HOST = os.environ["INVERTER_HOST"]
INVERTER_PORT = int(os.getenv("INVERTER_PORT", "502"))
UNIT_ID = int(os.getenv("UNIT_ID", "1"))
POLL_SECONDS = max(60, int(os.getenv("POLL_SECONDS", "60")))  # nie unter 60 s
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TABLE = os.getenv("SUPABASE_TABLE", "solar_readings")
BUFFER_FILE = Path(__file__).with_name("buffer.jsonl")
MAX_BUFFER_ROWS = 20_000  # ca. 2 Wochen bei 1 Messung/Minute

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("saj")

_running = True


def _stop(*_):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


# ---------- Modbus ----------

def i16(v: int) -> int:
    return struct.unpack(">h", struct.pack(">H", v))[0]


def u32(regs: list[int], i: int) -> int:
    return (regs[i] << 16) | regs[i + 1]


class Inverter:
    def __init__(self):
        self.client = ModbusTcpClient(INVERTER_HOST, port=INVERTER_PORT, timeout=10)
        self._unit_kw = None  # "device_id" (pymodbus >= 3.10) oder "slave"

    def _read(self, address: int, count: int) -> list[int]:
        if not self.client.connected and not self.client.connect():
            raise ConnectionError(f"Keine Verbindung zu {INVERTER_HOST}:{INVERTER_PORT}")
        kws = [self._unit_kw] if self._unit_kw else ["device_id", "slave"]
        for kw in kws:
            try:
                r = self.client.read_holding_registers(address, count=count, **{kw: UNIT_ID})
                self._unit_kw = kw
                break
            except TypeError:
                continue
        else:
            raise RuntimeError("Unbekannte pymodbus-API")
        if r.isError():
            raise IOError(f"Modbus-Fehler bei 0x{address:04X}: {r}")
        return r.registers

    def read_all(self) -> dict:
        # Block A: Batterie + PV-Strings (0x406E, 15 Register)
        a = self._read(16494, 15)
        time.sleep(0.3)
        # Block B: Leistungen (0x4095, 25 Register)
        b = self._read(16533, 25)
        time.sleep(0.3)
        # Block C: Energiezähler PV/Batterie (0x40BF, 32 Register, je 32 bit * 0.01 kWh)
        c = self._read(16575, 32)
        time.sleep(0.3)
        # Block D: Energiezähler Last/Netz (0x40DF, 32 Register)
        d = self._read(16607, 32)
        time.sleep(0.3)
        # Block E: Systemwerte wie in der Ampere-App (0x4204, 6 Register).
        # Am 06.10.2026 gegen die App verifiziert, Bilanz auf 1 W genau:
        #   0x4204 PV gesamt inkl. SolarMax (= 0x40A5 + 0x40A3)
        #   0x4205 Batterie (negativ = laden), 0x4206 Netz (negativ = Einspeisung)
        #   0x4207 Hausverbrauch, 0x4208 unbekannt (0), 0x4209 SoC in %
        e = self._read(16900, 6)

        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # Systemwerte (Block E) -- Grundlage fuer das Dashboard.
            # Vorzeichen entsprechen bereits der bestehenden Konvention:
            #   Batterie positiv = entladen, negativ = laden
            #   Netz     positiv = Bezug,    negativ = Einspeisung
            "sys_pv_power": i16(e[0]),
            "sys_battery_power": i16(e[1]),
            "sys_grid_power": i16(e[2]),
            "sys_house_power": i16(e[3]),
            "sys_soc": e[5],
            # Kompletter Rohblock 0x4095 als JSON -- solange die Zuordnung
            # einzelner Register noch nicht geklaert ist. Kann spaeter raus.
            "raw_power_block": b,
            # Momentanwerte in W
            # Gesamterzeugung beider Strings. Gegen den kWh-Zaehler mit
            # Faktor 1.00 bestaetigt (21.09.2026); ein dritter Eingang
            # existiert nicht (26.09.2026, Register 0x4077/78 = 0xFFFF).
            "pv_power": i16(b[16]),
            # Offset 14 ist NICHT der dritte String: es korreliert zwar mit
            # r = +0.97 zur PV, betraegt aber nur rund ein Drittel davon,
            # waehrend Offset 16 die Gesamterzeugung bereits vollstaendig
            # abbildet. Bleibt als eigene Spalte, bis geklaert ist, was es ist.
            "offset14_power": i16(b[14]),
            "battery_power": i16(b[17]),       # positiv = entladen, negativ = laden
            "grid_power": i16(b[24]),          # Richtung noch ungeklaert, s. README
            "total_grid_power": i16(b[18]),
            "load_power": i16(b[11]),
            "inverter_power": i16(b[20]),
            "direction_pv": b[0],
            "direction_battery": i16(b[1]),
            "direction_grid": i16(b[2]),
            # Batterie
            "soc": round(a[1] * 0.01, 2),
            "bat_temp": round(i16(a[0]) * 0.1, 1),
            # PV-Strings
            "pv1_voltage": round(a[3] * 0.1, 1),
            "pv1_current": round(a[4] * 0.01, 2),
            "pv1_power": a[5],
            "pv2_voltage": round(a[6] * 0.1, 1),
            "pv2_current": round(a[7] * 0.01, 2),
            "pv2_power": a[8],
            # Energie in kWh
            "pv_today_kwh": round(u32(c, 0) * 0.01, 2),
            "pv_total_kwh": round(u32(c, 6) * 0.01, 2),
            "bat_charge_today_kwh": round(u32(c, 8) * 0.01, 2),
            "bat_discharge_today_kwh": round(u32(c, 16) * 0.01, 2),
            "load_today_kwh": round(u32(d, 0) * 0.01, 2),
            # SAJ nennt diese Register "sell" und "feedin" -- aus Sicht des
            # Wechselrichters und damit genau andersherum, als man erwartet.
            # Am 13.09.2026 nachts verifiziert: ohne Sonne stieg nur "feedin".
            "grid_export_today_kwh": round(u32(d, 16) * 0.01, 2),   # SAJ: sell
            "grid_import_today_kwh": round(u32(d, 24) * 0.01, 2),   # SAJ: feedin
        }

    def close(self):
        self.client.close()


# ---------- Supabase ----------

def load_buffer() -> list[dict]:
    if not BUFFER_FILE.exists():
        return []
    rows = []
    for line in BUFFER_FILE.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def save_buffer(rows: list[dict]):
    rows = rows[-MAX_BUFFER_ROWS:]
    BUFFER_FILE.write_text("".join(json.dumps(r) + "\n" for r in rows))


def push(rows: list[dict]) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?on_conflict=ts"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }
    try:
        for i in range(0, len(rows), 500):
            r = requests.post(url, headers=headers, json=rows[i:i + 500], timeout=15)
            if r.status_code >= 300:
                log.error("Supabase %s: %s", r.status_code, r.text[:300])
                return False
        return True
    except requests.RequestException as e:
        log.error("Supabase nicht erreichbar: %s", e)
        return False


# ---------- Hauptschleife ----------

def main():
    inv = Inverter()
    pending = load_buffer()
    if pending:
        log.info("%d gepufferte Messwerte gefunden", len(pending))
    errors_in_row = 0

    while _running:
        started = time.monotonic()
        try:
            row = inv.read_all()
            errors_in_row = 0
            pending.append(row)
            bat = row["sys_battery_power"]
            grid = row["sys_grid_power"]
            log.info("PV %s W | Batterie %s W (%s), %s %% | Netz %s W (%s) | Haus %s W",
                     row["sys_pv_power"], abs(bat),
                     "laedt" if bat < 0 else "entlaedt", row["soc"],
                     abs(grid), "Bezug" if grid > 0 else "Einspeisung",
                     row["sys_house_power"])
        except Exception as e:
            errors_in_row += 1
            log.warning("Lesefehler (%d in Folge): %s", errors_in_row, e)
            inv.close()  # beim nächsten Durchlauf neu verbinden

        if pending:
            if push(pending):
                pending = []
                BUFFER_FILE.unlink(missing_ok=True)
            else:
                save_buffer(pending)

        # Bei wiederholten Fehlern länger warten, um das Modul nicht zu fluten
        wait = POLL_SECONDS * (1 if errors_in_row < 3 else 5)
        while _running and time.monotonic() - started < wait:
            time.sleep(1)

    inv.close()
    if pending:
        save_buffer(pending)
    log.info("Beendet")


if __name__ == "__main__":
    main()
