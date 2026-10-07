#!/usr/bin/env python3
"""
Einmaliger Verbindungstest (z. B. vom Mac aus), nutzt dieselbe .env
und dieselbe Auslese-Logik wie saj_logger.py.

  1. Modbus: Verbindung + Seriennummer + ein kompletter Messwert
  2. Supabase: Erreichbarkeit, Key und Tabelle prüfen
  3. Supabase: den Messwert als Zeile einfügen und zurücklesen

Aufruf:
  venv/bin/python test_mac.py            # alles testen, 1 Zeile schreiben
  venv/bin/python test_mac.py --dry-run  # nichts in Supabase schreiben
"""
import json
import struct
import sys

import requests

from saj_logger import (Inverter, INVERTER_HOST, INVERTER_PORT,
                        SUPABASE_URL, SUPABASE_KEY, TABLE, push)

DRY_RUN = "--dry-run" in sys.argv
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def ok(msg):   print(f"  ✅ {msg}")
def fail(msg): print(f"  ❌ {msg}")


def test_modbus():
    print(f"\n1) Modbus: {INVERTER_HOST}:{INVERTER_PORT}")
    inv = Inverter()
    try:
        regs = inv._read(0x8F00, 29)
        sn = b"".join(struct.pack(">H", r) for r in regs[3:13])
        ok(f"Verbunden, Seriennummer {sn.decode('ascii', 'replace').strip(chr(0) + ' ')}")
        row = inv.read_all()
        ok("Messwerte gelesen:")
        for k, v in row.items():
            print(f"     {k:<24} {v}")
        return row
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
        return None
    finally:
        inv.close()


def test_supabase_read():
    print(f"\n2) Supabase: {SUPABASE_URL}  Tabelle '{TABLE}'")
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?select=id&limit=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
    except requests.exceptions.SSLError as e:
        fail(f"SSL-Problem (Zertifikat?): {e}")
        return False
    except requests.RequestException as e:
        fail(f"Server nicht erreichbar: {e}")
        return False

    if r.status_code == 200:
        ok("Erreichbar, Key gültig, Tabelle vorhanden")
        return True
    if r.status_code in (401, 403):
        fail(f"Key abgelehnt ({r.status_code}). Ist es der service_role-Key?")
    elif r.status_code == 404 or "PGRST205" in r.text or "42P01" in r.text:
        fail(f"Tabelle '{TABLE}' nicht gefunden. schema.sql ausgeführt?")
    else:
        fail(f"HTTP {r.status_code}")
    print(f"     Antwort: {r.text[:300]}")
    return False


def test_supabase_write(row):
    print("\n3) Supabase: Testzeile schreiben")
    if DRY_RUN:
        print("  ⏭  übersprungen (--dry-run)")
        return
    if not push([row]):
        fail("Einfügen fehlgeschlagen (Details oben im Log)")
        return
    # params= sorgt dafür, dass das "+" der Zeitzone korrekt kodiert wird
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{TABLE}", headers=HEADERS, timeout=15,
                     params={"ts": f"eq.{row['ts']}", "select": "id,ts,pv_power,soc"})
    data = r.json() if r.ok else []
    if data:
        ok(f"Geschrieben und zurückgelesen: {json.dumps(data[0])}")
    else:
        fail(f"Zeile nach dem Einfügen nicht gefunden: {r.status_code} {r.text[:200]}")


if __name__ == "__main__":
    row = test_modbus()
    sb_ok = test_supabase_read()
    if row and sb_ok:
        test_supabase_write(row)
    elif sb_ok:
        print("\n3) Übersprungen: kein Messwert vom Wechselrichter")
    print()
