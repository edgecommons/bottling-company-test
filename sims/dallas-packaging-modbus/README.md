# Dallas packaging-line host Modbus simulator

The Line-2 (`gw-pack-01`) packaging dashboard reads its case/quality/machine signals from an
**external host** Modbus/TCP source (`HOST_MODBUS`), not from an in-container sim. This is that
source — a Dallas-owned scenario simulator serving the register map from
`edge-console/experiments/gemba/mockups/SIMULATOR_CONFIGURATION.md` ("External host Modbus
additions") and driving it with a coherent, causal packaging scenario (case counting, motor-current
precursor → jam → paused throughput → recovery reject burst, carton-magazine sawtooth) so the sample
UIs and telemetry-processor OEE have realistic data.

It is intentionally separate from `modbus-adapter/validation/modbus_sim_server.py` (the generic
adapter smoke fixture that backs `ggcommons-modbus-sim` on `:5020`), per that doc's guidance to keep
Dallas scenario logic out of the adapter validation fixtures.

## Register map (0-based PDU addresses, unit 1)

| Table | Addr | Name | Type / scale |
|-------|------|------|--------------|
| input | 0–1 | `GoodCaseCount` | uint32 (hi word first) |
| input | 2–3 | `CaseRejectCount` | uint32 (hi word first) |
| input | 4 | `PackerMotorCurrentA` | uint16 × 0.01 → A |
| input | 5 | `CaseWeightKg` | uint16 × 0.01 → kg |
| input | 6 | `CaseRateCpm` | uint16 × 0.1 → CPM |
| input | 7 | `CartonMagazinePct` | uint16 × 0.1 → % |
| discrete | 0 | `JamStatus` | bool |
| discrete | 1 | `MagazineLow` | bool |
| discrete | 2 | `CaseAtDischarge` | bool |
| discrete | 3 | `EStopHealthy` | bool |
| coil | 0 | `RunCommand` | bool (seeded true) |

uint32 values are encoded **high word first** (matching the harness float word order). The packaging
adapter catalog must read them with the same word order.

## Deploy

Runs as a standalone host container (like the permanent `ggcommons-modbus-sim`), on a **separate
port** so it never disturbs the `:5020` validation fixture:

```bash
docker build -t dallas-packaging-modbus-sim:latest sims/dallas-packaging-modbus
docker run -d --restart unless-stopped --name dallas-packaging-modbus-sim \
  -p 5021:5021 dallas-packaging-modbus-sim:latest
```

The packaging line reaches it via `HOST_MODBUS` (default `192.168.1.224:5021`).

## Scenario scope

The scenario is self-contained (Modbus-only). Coordinating Kepware's pallet/OEE tags to reproduce
these authoritative `GoodCaseCount` / `CaseRejectCount` (the shared scenario clock in
SIMULATOR_CONFIGURATION.md) is a separate cross-system step; this sim owns the authoritative counts.
