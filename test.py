import os
import struct
from pathlib import Path

from dotenv import load_dotenv
from pymodbus.client import ModbusTcpClient

load_dotenv(Path(__file__).with_name(".env"))

c = ModbusTcpClient(os.environ["INVERTER_HOST"],
                    port=int(os.getenv("INVERTER_PORT", "502")), timeout=5)
print("Verbunden:", c.connect())
try:
    r = c.read_holding_registers(0x8F00, count=29, device_id=1)
except TypeError:  # ältere pymodbus-Versionen
    r = c.read_holding_registers(0x8F00, count=29, slave=1)
if r.isError():
    print("Fehler:", r)
else:
    sn = b"".join(struct.pack(">H", x) for x in r.registers[3:13])
    print("Seriennummer:", sn.decode("ascii", "replace").strip("\x00 "))
c.close()