"""Dallas Line-2 scenario coordinator — Kepware pallet/OEE tags in lock-step with the Modbus counts.

SIMULATOR_CONFIGURATION.md requires the Line-2 OPC UA (Kepware) pallet/order/OEE tags to *reproduce
the host Modbus simulator's authoritative counts* from a shared scenario clock — `good =
GoodCaseCount`, `total = GoodCaseCount + CaseRejectCount`, pallet progress paused and
`PalletizerState = BLOCKED` while the Modbus line is jammed. The Kepware Simulator driver can't do
that on its own, so this small coordinator is the shared clock: it reads the Dallas packaging Modbus
sim (the authoritative source) and writes the derived pallet/order/OEE values into the Kepware
writable `Live.*` tags over OPC UA at 1 Hz.

It writes ONLY the coordinated (writable) tags. The condition signals `VisionPassPct` / `GlueTempC` /
`CaseWeightKg` are self-driving Kepware SINE functions and are left alone.

Derivations (120 cases/pallet, 24 cases/layer, 5 layers):
  PalletCaseCount  = 1..120 within the current pallet (0 only before the first case)
  PalletNumber     = completed 120-case pallets so far
  PalletLayer      = 1..5, advancing every 24 cases
  PalletizerState  = BLOCKED (jam) | DISCHARGING (pallet just finished) | WRAPPING (>=115) | BUILDING
  ActiveOrder      = SPRK-LIME-355-24 (the primary-scenario order)
  LabelCode        = lot/minute-derived printable code
  OeeShiftSnapshot = [plannedMs, runMs, total, good, 2142.857]  (ideal 2142.857 ms/case = 28 CPM)

Env: MODBUS_HOST, MODBUS_PORT (default 192.168.1.224:5021); KEPWARE_ENDPOINT, KEPWARE_USER,
KEPWARE_PASS.
"""
import asyncio
import os
import time

from asyncua import Client, ua
from pymodbus.client import ModbusTcpClient

MODBUS_HOST = os.environ.get("MODBUS_HOST", "192.168.1.224")
MODBUS_PORT = int(os.environ.get("MODBUS_PORT", "5021"))
KEP_ENDPOINT = os.environ.get("KEPWARE_ENDPOINT", "opc.tcp://192.168.1.180:49320")
KEP_USER = os.environ.get("KEPWARE_USER")
KEP_PASS = os.environ.get("KEPWARE_PASS")
if not KEP_USER or not KEP_PASS:
    raise SystemExit(
        "KEPWARE_USER and KEPWARE_PASS must be set in the environment "
        "(see ../../.env.example); credentials are never defaulted in source."
    )

NS = 2  # "Kepware Server" namespace
BASE = "GGCommonsTest.Device1.Live"
CASES_PER_PALLET = 120
CASES_PER_LAYER = 24
IDEAL_MS_PER_CASE = 2142.857  # 60000 / 28 CPM ideal (Line-2 OEE performance basis)
TICK_S = 1.0
ACTIVE_ORDER = "SPRK-LIME-355-24"


def _node(name, vtype):
    return f"ns={NS};s={BASE}.{name}", vtype


# writable Kepware tags this coordinator owns
TAGS = {
    "PalletCaseCount": ua.VariantType.UInt16,
    "PalletNumber": ua.VariantType.UInt32,
    "PalletLayer": ua.VariantType.UInt16,
    "PalletizerState": ua.VariantType.String,
    "ActiveOrder": ua.VariantType.String,
    "LabelCode": ua.VariantType.String,
    "OeeShiftSnapshot": ua.VariantType.Double,  # array
}


def _u32(regs):
    return (regs[0] << 16) | regs[1]


def read_modbus(client):
    """Return (good, rejects, jammed, running) or None if the read fails."""
    ir = client.read_input_registers(0, count=4, device_id=1)
    di = client.read_discrete_inputs(0, count=1, device_id=1)
    co = client.read_coils(0, count=1, device_id=1)
    if ir.isError() or di.isError() or co.isError():
        return None
    good = _u32(ir.registers[0:2])
    rejects = _u32(ir.registers[2:4])
    return good, rejects, bool(di.bits[0]), bool(co.bits[0])


def derive(good, rejects, jammed, prev_pallets, shift_start, run_ms):
    pallet_case = 0 if good == 0 else ((good - 1) % CASES_PER_PALLET) + 1
    pallet_number = good // CASES_PER_PALLET
    layer = min(5, (pallet_case - 1) // CASES_PER_LAYER + 1) if pallet_case else 1
    just_completed = pallet_number > prev_pallets
    if jammed:
        state = "BLOCKED"
    elif just_completed or pallet_case == CASES_PER_PALLET:
        state = "DISCHARGING"
    elif pallet_case >= 115:
        state = "WRAPPING"
    else:
        state = "BUILDING"
    minute = int((time.time() - shift_start) // 60)
    label = f"LS355-{pallet_number:03d}-{minute:04d}"
    planned_ms = (time.time() - shift_start) * 1000.0
    snapshot = [planned_ms, run_ms, float(good + rejects), float(good), IDEAL_MS_PER_CASE]
    return {
        "PalletCaseCount": pallet_case,
        "PalletNumber": pallet_number,
        "PalletLayer": layer,
        "PalletizerState": state,
        "ActiveOrder": ACTIVE_ORDER,
        "LabelCode": label,
        "OeeShiftSnapshot": snapshot,
    }, pallet_number


async def write_tags(nodes, values):
    for name, val in values.items():
        node, vtype = nodes[name]
        variant = ua.Variant(val, vtype)  # arrays: a list with a scalar VariantType is an array
        dv = ua.DataValue(Value=variant, SourceTimestamp=None, ServerTimestamp=None)
        await node.write_value(dv)


async def run():
    mb = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT, timeout=2)
    shift_start = time.time()
    run_ms = 0.0
    prev_pallets = 0
    print(f"[coordinator] Modbus {MODBUS_HOST}:{MODBUS_PORT} -> Kepware {KEP_ENDPOINT}", flush=True)
    while True:
        client = Client(KEP_ENDPOINT)
        client.set_user(KEP_USER)
        client.set_password(KEP_PASS)
        try:
            await client.connect()
            nodes = {n: (client.get_node(nid), vt) for n, (nid, vt) in
                     ((k, _node(k, v)) for k, v in TAGS.items())}
            print("[coordinator] connected to Kepware; driving Live.* tags", flush=True)
            last = time.time()
            while True:
                if not mb.connected and not mb.connect():
                    await asyncio.sleep(TICK_S)
                    continue
                now = time.time()
                dt_ms = (now - last) * 1000.0
                last = now
                data = read_modbus(mb)
                if data is None:
                    await asyncio.sleep(TICK_S)
                    continue
                good, rejects, jammed, running = data
                if running and not jammed:
                    run_ms += dt_ms
                values, prev_pallets = derive(good, rejects, jammed, prev_pallets, shift_start, run_ms)
                await write_tags(nodes, values)
                await asyncio.sleep(TICK_S)
        except Exception as e:  # noqa: BLE001 — reconnect on any OPC UA / transport error
            print(f"[coordinator] Kepware session error: {e}; reconnecting in 5s", flush=True)
            await asyncio.sleep(5)
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
