#!/usr/bin/env python3
"""
Roh-Dump des Leistungs-Registerblocks (0x4095, 25 Register).

Zeigt jedes Register des Blocks vorzeichenbehaftet und vorzeichenlos an
und rechnet aus, welche Kombinationen die Energiebilanz erfuellen
wuerden. Damit laesst sich zuordnen, welches Register wirklich der
Netzanschlusspunkt ist.

Am aussagekraeftigsten bei einem eindeutigen Betriebszustand, z. B.
mittags bei kraeftiger Einspeisung oder abends bei reinem Netzbezug.
Beim Ausfuehren notieren, was die Ampere Home App in diesem Moment
anzeigt (PV, Batterie, Netz, Hausverbrauch) -- ohne diese Referenz
ist der Dump nur halb so nuetzlich.

Aufruf:
  venv/bin/python dump_registers.py
  venv/bin/python dump_registers.py --csv dump.csv     # zusaetzlich als CSV
  venv/bin/python dump_registers.py --block strings    # PV-Strings statt Leistungen
  venv/bin/python dump_registers.py --block both       # beide Bloecke + Bilanz
  venv/bin/python dump_registers.py --block 0x406E --count 15
"""
import csv
import sys

from saj_logger import Inverter, i16

BLOECKE = {
    # Leistungen, Richtungen (Standard)
    "power": (16533, 25, {          # 0x4095
        0: "directionPV", 1: "directionBattery", 2: "directionGrid",
        3: "directionOutput", 4: "directionReserved",
        11: "TotalLoadPower", 14: "? evtl. PV3", 16: "pvPower (String 1+2)",
        17: "batteryPower", 18: "totalgridPower", 20: "inverterPower",
        24: "gridPower",
    }),
    # Batterie + PV-Strings. Je Eingang 3 Register: Spannung, Strom, Leistung.
    # Gibt es einen dritten Eingang, steht er auf den Offsets 9-11.
    "strings": (16494, 15, {        # 0x406E
        0: "batTemperature (x0.1)", 1: "batEnergyPercent (x0.01)",
        3: "pv1Volt (x0.1)", 4: "pv1Curr (x0.01)", 5: "pv1Power",
        6: "pv2Volt (x0.1)", 7: "pv2Curr (x0.01)", 8: "pv2Power",
        9: "pv3Volt? (x0.1)", 10: "pv3Curr? (x0.01)", 11: "pv3Power?",
        12: "pv4Volt? (x0.1)", 13: "pv4Curr? (x0.01)", 14: "pv4Power?",
    }),
}


def parse_args():
    args = sys.argv[1:]
    name = "power"
    if "--block" in args:
        name = args[args.index("--block") + 1]
    if name == "both":
        start, count, known = BLOECKE["power"]
    elif name in BLOECKE:
        start, count, known = BLOECKE[name]
    else:
        start, count, known = int(name, 0), 25, {}
    if "--count" in args:
        count = int(args[args.index("--count") + 1])
    return start, count, known


BLOCK_START, BLOCK_COUNT, KNOWN = parse_args()


def both():
    """Liest beide Bloecke kurz nacheinander und stellt die Bilanz auf."""
    inv = Inverter()
    try:
        pw = inv._read(16533, 25)
        st = inv._read(16494, 15)
    finally:
        inv.close()

    pv1, pv2 = st[5], st[8]
    pv_reg, bat, load = i16(pw[16]), i16(pw[17]), i16(pw[11])
    off14, grid = i16(pw[14]), i16(pw[24])

    print(f"\nLeistungsblock 0x4095 komplett")
    print(f"  {'Off':>3}  {'signed':>7}  Bekannte Zuordnung")
    for i, raw in enumerate(pw):
        if raw == 0:
            continue
        print(f"  {i:>3}  {i16(raw):>7}  {BLOECKE['power'][2].get(i, '')}")

    print("\nStrings")
    print(f"  String 1        {st[3]*0.1:7.1f} V  {st[4]*0.01:6.2f} A  {pv1:6} W")
    print(f"  String 2        {st[6]*0.1:7.1f} V  {st[7]*0.01:6.2f} A  {pv2:6} W")
    print(f"  Summe                                {pv1 + pv2:6} W")
    print(f"  pvPower (Reg 16)                     {pv_reg:6} W"
          f"   Differenz {pv_reg - pv1 - pv2:+} W")

    print("\nLeistungen")
    print(f"  Haus (11)       {load:7} W")
    print(f"  Batterie (17)   {bat:7} W   {'laedt' if bat < 0 else 'entlaedt'}")
    print(f"  Netz (24)       {grid:7} W   {'Bezug' if grid > 0 else 'Einspeisung'}")
    print(f"  Offset 14       {off14:7} W")
    print(f"  SoC             {st[1]*0.01:7.1f} %")

    print("\nBilanz  (Quellen - Verbraucher, Ziel: 0)")
    for name, quelle in (("mit pvPower", pv_reg), ("mit String 1+2", pv1 + pv2)):
        ohne = quelle + max(0, bat) + grid - load - max(0, -bat)
        print(f"  {name:<16} ohne Offset 14: {ohne:+6} W"
              f"   mit Offset 14: {ohne + off14:+6} W")
    print("\n  Welche Variante nahe 0 liegt, zeigt die richtige Deutung.")
    print()


def main():
    if "--block" in sys.argv and sys.argv[sys.argv.index("--block") + 1] == "both":
        return both()
    inv = Inverter()
    try:
        regs = inv._read(BLOCK_START, BLOCK_COUNT)
    finally:
        inv.close()

    print(f"\nBlock 0x{BLOCK_START:04X}, {BLOCK_COUNT} Register\n")
    print(f"{'Off':>3}  {'Adresse':>8}  {'roh':>6}  {'signed':>7}  Bekannte Zuordnung")
    print("-" * 62)
    rows = []
    for i, raw in enumerate(regs):
        sig = i16(raw)
        name = KNOWN.get(i, "")
        print(f"{i:>3}  0x{BLOCK_START + i:04X}  {raw:>6}  {sig:>7}  {name}")
        rows.append({"offset": i, "address": hex(BLOCK_START + i),
                     "raw": raw, "signed": sig, "known_name": name})

    if BLOCK_START != 16533:
        if "--csv" in sys.argv:
            _csv(rows)
        print()
        return

    # Bilanz: PV + Batterie(entladen) + Netz(Bezug) sollte dem Hausverbrauch
    # entsprechen. Welches Register passt als Netzwert am besten?
    pv, bat, load = i16(regs[16]), i16(regs[17]), i16(regs[11])
    rest = load - pv - bat
    print(f"\nHausverbrauch {load} W - PV {pv} W - Batterie {bat} W"
          f"  =>  aus dem Netz muessten {rest} W kommen.")
    print("\nWelche Register kommen diesem Wert nahe?")
    for i, raw in enumerate(regs):
        sig = i16(raw)
        for label, val in (("", sig), ("negiert ", -sig)):
            if val and abs(val - rest) <= max(30, abs(rest) * 0.15):
                print(f"   Offset {i:>2} (0x{BLOCK_START + i:04X}): "
                      f"{label}{val} W  {KNOWN.get(i, '')}")

    if "--csv" in sys.argv:
        _csv(rows)
    print()


def _csv(rows):
    path = sys.argv[sys.argv.index("--csv") + 1]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV geschrieben: {path}")


if __name__ == "__main__":
    main()
