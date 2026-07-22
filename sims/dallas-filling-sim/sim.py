"""Dallas filling-line (gw-fill-01) field simulator — one process, OPC UA + Modbus, shared scenario.

The filling line's OPC UA and Modbus sources run IN the edge-node container (the adapters read
localhost:4840 / localhost:5020). This Dallas-owned sim replaces the two generic adapter validation
fixtures for that line, implementing the "Line 1 — filling dashboard" additions from
`edge-console/experiments/gemba/mockups/SIMULATOR_CONFIGURATION.md`.

Running both protocols in ONE process lets a single shared scenario drive them, so the OPC UA
`OeeShiftSnapshot` reproduces the Modbus `GoodBottleCount`/`RejectCount` exactly (the doc's
requirement: "The Line 1 snapshot must read the same scenario state as the scalar Modbus counters …
It must not maintain an independent production count.").

The existing smoke signals are kept for compatibility: OPC UA `Simulation/{Sine1,Sine2,Counter,
Setpoint}`; Modbus holding 0 (BottleCount), 1-2 (ProductTemp f32), 40 (FillLevel).

Additions
  OPC UA `Simulation/Line1/`:
    LineSpeedBpm(Double) FillPressureKpa(Double) FillVolumeMl(Double) ValveTrackingCount(UInt16)
    CO2Volumes(Double) FillerState(String) ActiveRecipe(String) OeeShiftSnapshot(Double[5])
  Modbus:
    holding 50-51 GoodBottleCount u32 · 52-53 RejectCount u32 · 54-55 UnderfillRejectCount u32
    56-57 OverfillRejectCount u32 · 58-59 CapRejectCount u32 · 60 ConveyorSpeedPct x0.1
    61 BowlLevelPct x0.1 · 62 ProductTempC int16 x0.1
    discrete 0 ConveyorRunning · 1 InfeedStarved · 2 EStopHealthy

Scenario: ~126 BPM production around a 132 target; a periodic fill-pressure-drift episode (112→~140
kPa, 45-90 s) that biases fill volume high, slows the line, and lifts under/overfill rejects; a
carton/valve/infeed texture. OeeShiftSnapshot = [plannedMs, runMs, total=good+rejects, good,
454.545] (ideal 454.545 ms/bottle = 132 BPM).

Run:  python sim.py   (binds opc.tcp://localhost:4840/ and 0.0.0.0:5020)
"""
import asyncio
import random
import struct
import threading
import time

from asyncua import Server, ua
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartTcpServer

UNIT = 1
FC_DISCRETE, FC_HOLDING = 2, 3
OPCUA_ENDPOINT = "opc.tcp://localhost:4840/"
MODBUS_PORT = 5020
NS_URI = "urn:edgecommons:sim"
NOMINAL_BPM = 126.0
TARGET_BPM = 132.0
IDEAL_MS_PER_BOTTLE = 454.545  # 60000 / 132
TICK_S = 0.25


def f32_regs(v):
    b = struct.pack(">f", float(v))
    return [int.from_bytes(b[0:2], "big"), int.from_bytes(b[2:4], "big")]


def u32_regs(v):
    v = int(v) & 0xFFFFFFFF
    return [(v >> 16) & 0xFFFF, v & 0xFFFF]


class Scenario:
    """Shared filling-line state, advanced once per tick; both protocols read from it."""

    def __init__(self):
        self.good = 0
        self.underfill = 0
        self.overfill = 0
        self.cap = 0
        self.bottle_accum = 0.0
        self.bowl = 82.0
        self.shift_start = time.time()
        self.run_ms = 0.0
        # pressure-drift episode: idle -> drift -> recover
        self.phase = "idle"
        self.timer = 0.0
        self.next_in = random.uniform(120, 240)
        self.pressure = 112.4
        self.valve_tracking = 40
        self.infeed_starved = False
        self.starve_timer = 0.0
        self.next_starve = random.uniform(720, 1080)

    @property
    def rejects(self):
        return self.underfill + self.overfill + self.cap

    def _bpm(self):
        base = NOMINAL_BPM if self.phase != "drift" else 114.0
        return 0.0 if self.infeed_starved else base + random.uniform(-1.8, 1.8)

    def tick(self, dt):
        # infeed-starve episodes (short, occasional)
        if self.infeed_starved:
            self.starve_timer -= dt
            if self.starve_timer <= 0:
                self.infeed_starved = False
                self.next_starve = random.uniform(720, 1080)
        else:
            self.next_starve -= dt
            if self.next_starve <= 0:
                self.infeed_starved = True
                self.starve_timer = random.uniform(10, 25)

        # pressure-drift episode state machine
        if self.phase == "idle":
            self.pressure = 112.4 + random.uniform(-0.8, 0.8)
            self.next_in -= dt
            if self.next_in <= 0:
                self.phase, self.timer = "drift", random.uniform(45, 90)
        elif self.phase == "drift":
            self.pressure = min(144.0, self.pressure + (140.0 - 112.4) * dt / 30.0) + random.uniform(-0.6, 0.6)
            self.timer -= dt
            if self.timer <= 0:
                self.phase = "recover"
        elif self.phase == "recover":
            self.phase = "idle"
            self.next_in = random.uniform(120, 240)

        running = not self.infeed_starved
        if running:
            self.run_ms += dt * 1000.0
            self.bottle_accum += self._bpm() / 60.0 * dt
            while self.bottle_accum >= 1.0:
                self.bottle_accum -= 1.0
                self.good += 1
                self.bowl -= 0.06
                if self.bowl <= 60.0:
                    self.bowl = 84.0
                # reject texture — under/overfill rise during the pressure drift
                if random.random() < (0.010 if self.phase == "drift" else 0.001):
                    self.overfill += 1
                if random.random() < (0.008 if self.phase == "drift" else 0.001):
                    self.underfill += 1
                if random.random() < 0.004:
                    self.cap += 1
        # slow bowl-level control oscillation + valve texture
        self.bowl += random.uniform(-0.2, 0.2)
        self.valve_tracking = 39 if self.phase == "drift" and random.random() < 0.3 else 40

    # ---- projections ----
    def filler_state(self):
        if self.infeed_starved:
            return "STARVED"
        if self.phase == "drift":
            return "PRESSURE_HOLD"
        return "RUNNING"

    def fill_volume(self):
        bias = 1.4 if self.phase == "drift" else 0.0
        return 500.2 + bias + random.uniform(-0.6, 0.6)

    def snapshot(self):
        planned = (time.time() - self.shift_start) * 1000.0
        # run_ms accumulates a fixed dt per tick (a logical clock) while `planned` is wall-clock;
        # on host sleep/resume the two can diverge and run_ms overruns planned. The OEE Lua rejects
        # any snapshot with runMs > plannedMs, so clamp to keep the invariant (run is a subset of planned).
        run = min(self.run_ms, planned)
        return [planned, run, float(self.good + self.rejects), float(self.good), IDEAL_MS_PER_BOTTLE]

    def holding_block(self):
        # index-aligned to a 0-based holding block
        hr = [0] * 64
        hr[0] = self.good & 0xFFFF                      # BottleCount (legacy uint16)
        hr[1], hr[2] = f32_regs(self.product_temp())    # ProductTemp f32 (legacy)
        hr[40] = int(round(max(0.0, self.bowl) * 10))   # FillLevel x0.1 (legacy: reuse bowl level)
        hr[41] = 0b0000_1000
        hr[50:52] = u32_regs(self.good)
        hr[52:54] = u32_regs(self.rejects)
        hr[54:56] = u32_regs(self.underfill)
        hr[56:58] = u32_regs(self.overfill)
        hr[58:60] = u32_regs(self.cap)
        hr[60] = int(round((96.0 if not self.infeed_starved else 20.0) * 10))  # ConveyorSpeedPct x0.1
        hr[61] = int(round(max(0.0, self.bowl) * 10))   # BowlLevelPct x0.1
        hr[62] = int(round(self.product_temp() * 10)) & 0xFFFF  # ProductTempC int16 x0.1
        return hr

    def product_temp(self):
        return 4.3 + random.uniform(-0.15, 0.15)

    def discrete_block(self):
        return [int(not self.infeed_starved), int(self.infeed_starved), 1]  # ConveyorRunning, InfeedStarved, EStopHealthy


def build_modbus():
    store = ModbusSlaveContext(
        co=ModbusSequentialDataBlock(0, [0] * 8),
        di=ModbusSequentialDataBlock(0, [0] * 8),
        hr=ModbusSequentialDataBlock(0, [0] * 64),
        ir=ModbusSequentialDataBlock(0, [12345] + [0] * 7),
        zero_mode=True,
    )
    return ModbusServerContext(slaves=store, single=True), store


async def main():
    scn = Scenario()
    mb_ctx, mb_store = build_modbus()
    threading.Thread(target=lambda: StartTcpServer(context=mb_ctx, address=("0.0.0.0", MODBUS_PORT)),
                     daemon=True).start()

    server = Server()
    await server.init()
    server.set_endpoint(OPCUA_ENDPOINT)
    server.set_server_name("EdgeCommons Dallas Filling Sim")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx = await server.register_namespace(NS_URI)

    sim = await server.nodes.objects.add_folder(ua.NodeId("Simulation", idx),
                                                ua.QualifiedName("Simulation", idx))
    sine1 = await sim.add_variable(ua.NodeId("Sine1", idx), ua.QualifiedName("Sine1", idx), 0.0)
    sine2 = await sim.add_variable(ua.NodeId("Sine2", idx), ua.QualifiedName("Sine2", idx), 0.0)
    counter = await sim.add_variable(ua.NodeId("Counter", idx), ua.QualifiedName("Counter", idx), 0)
    setpoint = await sim.add_variable(ua.NodeId("Setpoint", idx), ua.QualifiedName("Setpoint", idx), 0.0)
    await setpoint.set_writable(True)

    line1 = await sim.add_folder(ua.NodeId("Line1", idx), ua.QualifiedName("Line1", idx))

    async def var(name, val, vt):
        return await line1.add_variable(ua.NodeId(f"Line1.{name}", idx),
                                        ua.QualifiedName(name, idx), val, vt)

    n_speed = await var("LineSpeedBpm", 0.0, ua.VariantType.Double)
    n_press = await var("FillPressureKpa", 112.4, ua.VariantType.Double)
    n_vol = await var("FillVolumeMl", 500.2, ua.VariantType.Double)
    n_valve = await var("ValveTrackingCount", 40, ua.VariantType.UInt16)
    n_co2 = await var("CO2Volumes", 2.62, ua.VariantType.Double)
    n_state = await var("FillerState", "RUNNING", ua.VariantType.String)
    n_recipe = await var("ActiveRecipe", "LS-355-07", ua.VariantType.String)
    n_snap = await var("OeeShiftSnapshot", [0.0] * 5, ua.VariantType.Double)

    async with server:
        print(f"[dallas-filling-sim] OPC UA {OPCUA_ENDPOINT} + Modbus 0.0.0.0:{MODBUS_PORT}", flush=True)
        t = 0.0
        while True:
            scn.tick(TICK_S)
            t += TICK_S
            # legacy changing smoke signals
            import math
            await sine1.write_value(math.sin(t * 0.5))
            await sine2.write_value(math.cos(t * 0.3))
            await counter.write_value(int(t / TICK_S) & 0x7FFF)
            # Line1 scenario nodes
            await n_speed.write_value(ua.Variant(round(scn._bpm(), 2), ua.VariantType.Double))
            await n_press.write_value(ua.Variant(round(scn.pressure, 2), ua.VariantType.Double))
            await n_vol.write_value(ua.Variant(round(scn.fill_volume(), 2), ua.VariantType.Double))
            await n_valve.write_value(ua.Variant(int(scn.valve_tracking), ua.VariantType.UInt16))
            await n_co2.write_value(ua.Variant(round(2.62 + random.uniform(-0.03, 0.03), 3), ua.VariantType.Double))
            await n_state.write_value(ua.Variant(scn.filler_state(), ua.VariantType.String))
            await n_recipe.write_value(ua.Variant("LS-355-07", ua.VariantType.String))
            await n_snap.write_value(ua.Variant(scn.snapshot(), ua.VariantType.Double))
            # Modbus mirror
            mb_store.setValues(FC_HOLDING, 0, scn.holding_block())
            mb_store.setValues(FC_DISCRETE, 0, scn.discrete_block())
            await asyncio.sleep(TICK_S)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
