# Dallas site

The concrete site the harness stands up: one **site node** and two **line devices** (a filling line and a
packaging line), each a container running the standard edgecommons components against a shared Unified
Namespace. This directory holds the compose file, the supervisor process definitions, and the per-device
component **config catalogs** that wire it all together. For build/run/teardown of the whole plant see the
repository [`README.md`](../../README.md); this page explains the site's own layout.

## Topology

```
                       site broker (EMQX)  ── edge-console ── operator UI + hosted TV boards
                              ▲
             uns-bridge relays │ (per device)
        ┌─────────────────────┴─────────────────────┐
  filling-line (gw-fill-01)              packaging-line (gw-pack-01)
  opcua + modbus adapters                opcua + modbus adapters
  telemetry-processor (OEE)              telemetry-processor (OEE)
  file-replicator                        …
  dallas-filling-sim (in-container)      KepWare + host Modbus sim (LAN)
```

Each line device runs its adapters, a telemetry-processor, a file-replicator, and a `uns-bridge` that
relays the device-local bus up to the site broker; the site node runs the config-component and the
edge-console. The filling line is fully self-contained (its OPC UA + Modbus sources are the in-container
[`dallas-filling-sim`](../../sims/dallas-filling-sim/README.md)); the packaging line reaches LAN sources
(KepWare + the host [`dallas-packaging-modbus`](../../sims/dallas-packaging-modbus/README.md) sim).

## Layout

| Path | What it is |
|------|-----------|
| `docker-compose.yml` | The site stack — site node + the two line `edge-node` containers + brokers. |
| `supervisor/*.conf` | The supervised process set inside each container (`site.conf`, `filling-line.conf`, `packaging-line.conf`). |
| `configs/site/` | Site node: the config-component catalog + `console-messaging.json` (the console's broker binding). |
| `configs/filling-line/` | `config-catalog.json` (the components that run on `gw-fill-01`) plus each component's `*-messaging.json` (opcua, modbus, telemetry, file-replicator, uns-bridge, config-component). |
| `configs/packaging-line/` | `config-catalog.tmpl.json` (templated) + the packaging components' messaging configs. |
| `configs/lua/transform.lua` | The telemetry-processor's per-signal transform (device, signal, unit, rawValue, engValue, rate, alarm, …). |
| `configs/lua/oee/` | The OEE route: `availability.lua`, `performance.lua`, `quality.lua`, `oee.lua`. |

## The OEE pipeline

Each line's telemetry-processor consumes the adapter signals off the UNS and runs the `configs/lua/oee/`
scripts to derive **Availability × Performance × Quality = OEE**, publishing them back onto the bus for the
console and the TV boards to render. The filling line derives these from the sim's `OeeShiftSnapshot`
(`[plannedMs, runMs, good+rejects, good, idealMsPerBottle]`); the scripts reject any snapshot where
`runMs > plannedMs`, which is why the sim clamps `runMs` to `plannedMs`.

## Config catalogs

A line's `config-catalog*.json` is the list of components the device runs and the config each is given —
it is the single place that determines what runs on `gw-fill-01` vs `gw-pack-01`. Editing which adapter
reads which source, or which line publishes which signals, happens here (and in the referenced
`*-messaging.json` files), not in the component images. See the repository README's
[config semantics](../../README.md#note-on-hierarchical-config-semantics) note.

## Run

```bash
docker compose -f sites/dallas-site/docker-compose.yml up -d --build
```

(or the repository README's `Run it` recipe for the whole plant). The filling-line OEE surfaces on the
edge-console and the native Android TV Line 01 board; see the end-to-end
[Dallas line demo](https://docs.edgecommons.mbreissi.com/guides/dallas-line-demo/).

## Generated configuration - do not hand-edit

Everything under `configs/` and `supervisor/` is **generated** by the Deployment Studio kernel
(`ec-deploy`, in the local `deployment-studio/` repo) from the Dallas golden fixture
(`deployment-studio/fixtures/dallas/definition.yaml` + `layers/` + `bindings/local.json`).

To change anything here - endpoints, components, config values, start order - edit the fixture
(external endpoints live in `bindings/local.json`), then re-render and copy:

    cd ../deployment-studio/kernel
    cargo run -- render ../fixtures/dallas/definition.yaml --environment local --out /tmp/render

The old `config-catalog.tmpl.json` + `render-packaging-catalog` startup substitution is gone:
binding values are resolved at render time, and the packaging catalog is a static rendered file
mounted at the path the bootstrap's catalogSource declares.
