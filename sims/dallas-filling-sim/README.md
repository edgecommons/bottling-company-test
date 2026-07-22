# Dallas filling-line field simulator (`gw-fill-01`)

One process that serves **both OPC UA and Modbus/TCP** from a single shared scenario, so the OPC UA
`OeeShiftSnapshot` and the Modbus `GoodBottleCount`/`RejectCount` always agree (there is no independent
production count). It replaces the two generic adapter validation fixtures for the filling line and
implements the "Line 1 — filling dashboard" additions from
`edge-console/experiments/gemba/mockups/SIMULATOR_CONFIGURATION.md`.

Both sources run **inside the filling-line `edge-node` container**; the OPC UA and Modbus adapters read
`localhost:4840` / `localhost:5020`.

## Node / register map

**OPC UA** (`opc.tcp://localhost:4840/`, namespace `urn:edgecommons:sim`) — under `Simulation/Line1/`:

| Node | Type |
|------|------|
| `LineSpeedBpm` | Double |
| `FillPressureKpa` | Double |
| `FillVolumeMl` | Double |
| `ValveTrackingCount` | UInt16 |
| `CO2Volumes` | Double |
| `FillerState` | String |
| `ActiveRecipe` | String |
| `OeeShiftSnapshot` | Double[5] = `[plannedMs, runMs, total, good, idealMsPerBottle]` |

Legacy smoke nodes are retained for compatibility: `Simulation/{Sine1,Sine2,Counter,Setpoint}`.

**Modbus/TCP** (`0.0.0.0:5020`, unit 1, 0-based PDU addresses; uint32 = high word first):

| Table | Addr | Name | Type / scale |
|-------|------|------|--------------|
| holding | 50–51 | `GoodBottleCount` | uint32 |
| holding | 52–53 | `RejectCount` | uint32 |
| holding | 54–55 | `UnderfillRejectCount` | uint32 |
| holding | 56–57 | `OverfillRejectCount` | uint32 |
| holding | 58–59 | `CapRejectCount` | uint32 |
| holding | 60 | `ConveyorSpeedPct` | uint16 × 0.1 |
| holding | 61 | `BowlLevelPct` | uint16 × 0.1 |
| holding | 62 | `ProductTempC` | int16 × 0.1 |
| discrete | 0 | `ConveyorRunning` | bool |
| discrete | 1 | `InfeedStarved` | bool |
| discrete | 2 | `EStopHealthy` | bool |

Legacy smoke registers are retained: holding 0 (`BottleCount`), 1–2 (`ProductTemp` f32), 40 (`FillLevel`).

## Scenario

~126 BPM production around a 132-BPM target, with a periodic **fill-pressure-drift episode**
(112 → ~140 kPa over 45–90 s) that biases fill volume high, slows the line, and lifts under/overfill
rejects, plus carton/valve/infeed texture. `OeeShiftSnapshot = [plannedMs, runMs, good+rejects, good,
454.545]` (ideal 454.545 ms/bottle = 132 BPM). `runMs` is clamped to `plannedMs` so the snapshot always
satisfies the OEE processor's invariant (`runMs ≤ plannedMs`) across host sleep/resume.

## Run

```bash
python sim.py            # binds opc.tcp://localhost:4840/ and Modbus 0.0.0.0:5020
```

Built and run inside the filling-line container by the site harness (see the repository
[`README.md`](../../README.md)). To iterate against a running container without a rebuild:

```bash
docker cp sim.py dallas-filling-line:/opt/sims/dallas_filling_sim.py && docker restart dallas-filling-line
```

The filling-line OEE dashboards — the edge-console Signals/Overview screens and the native Android TV
Line 01 board — read the signals this sim produces. See the end-to-end
[Dallas line demo](https://docs.edgecommons.mbreissi.com/guides/dallas-line-demo/) walkthrough.
