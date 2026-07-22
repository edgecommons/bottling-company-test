# Dallas Line-2 scenario coordinator

`SIMULATOR_CONFIGURATION.md` requires the Line-2 Kepware pallet/order/OEE tags to **reproduce the
host Modbus simulator's authoritative counts** from a shared scenario clock. The Kepware Simulator
driver can't do that alone, so this coordinator *is* the shared clock: it reads the Dallas packaging
Modbus sim (the authoritative source, `sims/dallas-packaging-modbus` on `:5021`) and writes the
derived pallet/order/OEE values into the Kepware writable `Live.*` tags over OPC UA at 1 Hz.

It writes only the coordinated tags; the condition signals (`VisionPassPct`, `GlueTempC`,
`CaseWeightKg`) are self-driving Kepware SINE functions and are left alone.

## Derived tags

| Tag | Derivation |
|-----|-----------|
| `Live.PalletCaseCount` | 1–120 within the current pallet |
| `Live.PalletNumber` | completed 120-case pallets |
| `Live.PalletLayer` | 1–5, advancing every 24 cases |
| `Live.PalletizerState` | `BLOCKED` (Modbus jam) / `DISCHARGING` / `WRAPPING` / `BUILDING` |
| `Live.ActiveOrder` | `SPRK-LIME-355-24` |
| `Live.LabelCode` | lot/minute-derived code |
| `Live.OeeShiftSnapshot` | `[plannedMs, runMs, total=good+rejects, good, 2142.857]` |

`good`/`rejects` come straight from the Modbus `GoodCaseCount`/`CaseRejectCount`, so the OPC UA and
Modbus counts always agree — including across a jam (pallet progress pauses, state → `BLOCKED`).

## Run

```bash
docker build -t dallas-scenario-coordinator:latest sims/dallas-scenario-coordinator
docker run -d --restart unless-stopped --name dallas-scenario-coordinator \
  -e MODBUS_HOST=192.168.1.224 -e MODBUS_PORT=5021 \
  -e KEPWARE_ENDPOINT=opc.tcp://192.168.1.180:49320 \
  -e KEPWARE_USER=testuser -e KEPWARE_PASS=Password1234567 \
  dallas-scenario-coordinator:latest
```

OPC UA writes use a bare `DataValue` (no timestamp/status) — KEPServerEX rejects writes that carry
one (`BadWriteNotSupported`).
