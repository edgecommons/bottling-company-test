"""Dallas packaging-line host Modbus simulator.

The Line-2 (`gw-pack-01`) packaging dashboard reads its case/quality/machine signals from an
*external host* Modbus/TCP source (`HOST_MODBUS`), separate from the in-container Line-1 sim. This
is that source: a Dallas-owned scenario simulator that serves the register map defined in
`edge-console/experiments/gemba/mockups/SIMULATOR_CONFIGURATION.md` ("External host Modbus
additions") and drives it with a coherent, causal packaging scenario so the sample UIs and
telemetry-processor OEE have realistic data.

It is deliberately NOT the modbus-adapter validation fixture (`modbus-adapter/validation/
modbus_sim_server.py`): that stays a generic smoke slave. Keeping this Dallas scenario separate is
the guidance in SIMULATOR_CONFIGURATION.md ("Prefer Dallas-specific simulator modules owned by the
bottling harness").

Register map (0-based PDU addresses, unit/device id 1, `zero_mode`):

  input registers (FC 04, read-only measurements):
    0-1  GoodCaseCount        uint32  hi-word first; increments at CaseRateCpm while running & clear
    2-3  CaseRejectCount      uint32  hi-word first; 0.4-0.8% baseline + a burst after jam recovery
    4    PackerMotorCurrentA  uint16  scale 0.01 -> A; 6.2 A normal, ramps > 9 A before a jam
    5    CaseWeightKg         uint16  scale 0.01 -> kg; 12.18 +/- 0.03, occasional reject outlier
    6    CaseRateCpm          uint16  scale 0.1  -> CPM; 27.6 +/- 0.6, 0 while jammed
    7    CartonMagazinePct    uint16  scale 0.1  -> %; sawtooth depletion, jump to 100 % on refill

  discrete inputs (FC 02, read-only status):
    0    JamStatus            bool    scripted 10-20 s jam
    1    MagazineLow          bool    true below 15 %
    2    CaseAtDischarge      bool    pulses at case cadence while running
    3    EStopHealthy         bool    normally true

  coils (FC 01/05, read/write command):
    0    RunCommand           bool    line run/hold; seeded true

The scenario is self-contained (Modbus-only). Coordinating Kepware's pallet/OEE tags to reproduce
these authoritative counts (the shared scenario clock in SIMULATOR_CONFIGURATION.md) is the separate
cross-system step; this sim owns the authoritative GoodCaseCount / CaseRejectCount.

Run:  python sim.py [--host 0.0.0.0] [--port 5021]
"""
import argparse
import math
import random
import threading
import time

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartTcpServer

UNIT = 1

# Function codes for ModbusSlaveContext.setValues / getValues.
FC_COIL, FC_DISCRETE, FC_HOLDING, FC_INPUT = 1, 2, 3, 4

# Scenario constants (SIMULATOR_CONFIGURATION.md "Line 2 — packaging dashboard").
NOMINAL_CPM = 27.6           # cases per minute while running
MAG_DRAIN_PER_CASE = 0.42    # carton-magazine % consumed per case (sawtooth)
MAG_REFILL_AT = 12.0         # % that triggers a refill to 100 %
MAG_LOW_PCT = 15.0           # MagazineLow threshold
REJECT_RATE = 0.006          # 0.6 % baseline reject fraction
TICK_S = 0.25                # scenario update period


def u32_regs(v):
    """uint32 -> [hi, lo] (high word first), matching the harness float word order."""
    v &= 0xFFFFFFFF
    return [(v >> 16) & 0xFFFF, v & 0xFFFF]


class Scenario:
    """The packaging line's authoritative production state, advanced once per tick."""

    def __init__(self):
        self.good = 0
        self.rejects = 0
        self.magazine = 100.0
        self.motor_a = 6.2
        self.case_accum = 0.0          # fractional cases carried between ticks
        self.discharge_pulse = False
        # jam state machine: idle -> ramp -> jammed -> recover -> idle
        self.jam_phase = "idle"
        self.jam_timer = 0.0
        self.next_jam_in = random.uniform(150, 300)   # first jam 2.5-5 min out
        self.reject_burst = 0

    def _cpm(self):
        return 0.0 if self.jam_phase == "jammed" else NOMINAL_CPM + random.uniform(-0.6, 0.6)

    def tick(self, running, dt):
        # --- jam state machine (causal: motor ramps, then jam, then recovery burst) ---
        if self.jam_phase == "idle":
            self.next_jam_in -= dt
            self.motor_a = 6.2 + random.uniform(-0.15, 0.15)
            if running and self.next_jam_in <= 0:
                self.jam_phase, self.jam_timer = "ramp", random.uniform(20, 40)
        elif self.jam_phase == "ramp":
            self.jam_timer -= dt
            self.motor_a = min(9.6, self.motor_a + (9.4 - 6.2) * dt / 25.0)  # climb toward >9 A
            if self.jam_timer <= 0:
                self.jam_phase, self.jam_timer = "jammed", random.uniform(10, 20)
        elif self.jam_phase == "jammed":
            self.jam_timer -= dt
            self.motor_a = 6.2 + random.uniform(-0.1, 0.1)
            if self.jam_timer <= 0:
                self.jam_phase = "recover"
                self.reject_burst = random.randint(3, 7)      # brief quality dip on restart
        elif self.jam_phase == "recover":
            self.jam_phase = "idle"
            self.next_jam_in = random.uniform(150, 300)

        # --- case production while running and not jammed ---
        self.discharge_pulse = False
        if running and self.jam_phase != "jammed":
            self.case_accum += self._cpm() / 60.0 * dt
            while self.case_accum >= 1.0:
                self.case_accum -= 1.0
                self.good += 1
                self.discharge_pulse = True
                self.magazine -= MAG_DRAIN_PER_CASE
                if random.random() < REJECT_RATE:
                    self.rejects += 1
                if self.magazine <= MAG_REFILL_AT:
                    self.magazine = 100.0
        if self.reject_burst and running:
            self.rejects += 1
            self.reject_burst -= 1

    # ---- register projections ----
    def input_registers(self):
        weight = 12.18 + random.uniform(-0.03, 0.03)
        return (
            u32_regs(self.good)                                  # 0-1 GoodCaseCount
            + u32_regs(self.rejects)                             # 2-3 CaseRejectCount
            + [int(round(self.motor_a * 100))]                  # 4   PackerMotorCurrentA x0.01
            + [int(round(weight * 100))]                        # 5   CaseWeightKg x0.01
            + [int(round(self._cpm() * 10))]                    # 6   CaseRateCpm x0.1
            + [int(round(max(0.0, self.magazine) * 10))]        # 7   CartonMagazinePct x0.1
        )

    def discrete_inputs(self):
        return [
            self.jam_phase == "jammed",     # 0 JamStatus
            self.magazine < MAG_LOW_PCT,    # 1 MagazineLow
            self.discharge_pulse,           # 2 CaseAtDischarge
            True,                           # 3 EStopHealthy
        ]


def build_context():
    store = ModbusSlaveContext(
        co=ModbusSequentialDataBlock(0, [1] + [0] * 15),   # coil 0 RunCommand seeded true
        di=ModbusSequentialDataBlock(0, [0] * 16),
        hr=ModbusSequentialDataBlock(0, [0] * 16),
        ir=ModbusSequentialDataBlock(0, [0] * 16),
        zero_mode=True,
    )
    return ModbusServerContext(slaves=store, single=True), store


def driver(store):
    scn = Scenario()
    last = time.time()
    while True:
        now = time.time()
        dt = now - last
        last = now
        running = bool(store.getValues(FC_COIL, 0, count=1)[0])
        scn.tick(running, dt)
        store.setValues(FC_INPUT, 0, scn.input_registers())
        store.setValues(FC_DISCRETE, 0, [int(b) for b in scn.discrete_inputs()])
        time.sleep(TICK_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5021)
    args = ap.parse_args()

    context, store = build_context()
    threading.Thread(target=driver, args=(store,), daemon=True).start()
    print(f"[dallas-packaging-sim] Modbus/TCP on {args.host}:{args.port} (unit {UNIT})", flush=True)
    StartTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
