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

Each line device runs its adapters, a telemetry-processor, a config-component and a `uns-bridge`
that relays the device-local bus up to the site broker. Only the filling line runs file-replicator.
The site node runs a broker, config-component and edge-console. The filling line is fully self-contained
(its OPC UA + Modbus sources are the in-container
[`dallas-filling-sim`](../../sims/dallas-filling-sim/README.md)); the packaging line reaches LAN sources
(KepWare + the host [`dallas-packaging-modbus`](../../sims/dallas-packaging-modbus/README.md) sim).

## Layout

| Path | What it is |
|------|-----------|
| `definition.yaml` | **The source of truth.** One `DeploymentDefinition` — a shared `topology` (the plant) plus per-platform `profiles` (`host`, `greengrass`, `kubernetes`). This harness runs the **host** profile; the same topology also renders to Greengrass and Kubernetes. Device configs and supervisor files are the host render's output; `configs/lua/` is authored source. |
| `bindings/{local,prod,k8s}.json` | Per-environment values the definition references by `${binding:…}` (external endpoints — Kepware, the host Modbus sim). `local` answers the host profile; `prod`/`k8s` answer the other profiles. The answered half of the IaC handshake. |
| `layers/` | The hierarchical config layers the definition merges — per scope (`scopes/`), per component (`components/`), and the config-source provider (`provider/`). |
| `docker-compose.yml` | The site stack — site node + the two line `edge-node` containers + brokers. |
| `supervisor/*.conf` | The supervised process set inside each container (`site.conf`, `filling-line.conf`, `packaging-line.conf`). |
| `configs/site/` | Site node: the config-component catalog + `console-messaging.json` (the console's broker binding). |
| `configs/filling-line/` | `config-catalog.json` (the components that run on `gw-fill-01`) plus each component's `*-messaging.json` (opcua, modbus, telemetry, file-replicator, uns-bridge, config-component). |
| `configs/packaging-line/` | Rendered static `config-catalog.json` + the packaging components' messaging configs. |
| `configs/lua/transform.lua` | The telemetry-processor's per-signal transform (device, signal, unit, rawValue, engValue, rate, alarm, …). |
| `configs/lua/oee/` | The OEE route: `availability.lua`, `performance.lua`, `quality.lua`, `oee.lua`. |

## The OEE pipeline

Each line's telemetry-processor consumes the adapter signals off the UNS and runs the `configs/lua/oee/`
scripts to derive **Availability × Performance × Quality = OEE**, publishing them back onto the bus for the
console and the TV boards to render. The filling line derives these from the sim's `OeeShiftSnapshot`
(`[plannedMs, runMs, good+rejects, good, idealMsPerBottle]`); the scripts reject any snapshot where
`runMs > plannedMs`, which is why the sim clamps `runMs` to `plannedMs`.

## Config catalogs

A line's rendered `config-catalog.json` is the config served to the components on that device.
The authored `definition.yaml`, `layers/` and `bindings/` determine the component set, source
endpoints and signal configuration. Edit those inputs and regenerate the catalogs, messaging
configs and supervisor files together. See the repository README's
[config semantics](../../README.md#note-on-hierarchical-config-semantics) note.

## Run

```bash
docker compose -f sites/dallas-site/docker-compose.yml up -d --build
```

(or the repository README's `Run it` recipe for the whole plant). The filling-line OEE surfaces on the
edge-console and the native Android TV Line 01 board; see the end-to-end
[Dallas line demo](https://docs.edgecommons.mbreissi.com/guides/dallas-line-demo/).

## Generated configuration — do not hand-edit

Device configuration and `supervisor/` files are **rendered** from `definition.yaml` (plus its
`layers/` and `bindings/local.json`) by the `edgecommons` deployment renderer. The definition is the
source of truth; the config sources are its output. A hand edit to a generated file is drift, and the
`config-drift-gate` workflow catches it on every PR by rendering the definition and diffing.
The scripts under `configs/lua/` are authored source and remain outside this generated output set.

To change anything here — endpoints, components, config values, start order — edit the definition or
its layers (external endpoints live in `bindings/local.json`), then re-render in place:

    edgecommons deployment render sites/dallas-site/definition.yaml --env local --target HOST

That writes `sites/dallas-site/render/host/<node>/…`; copy each node's files onto its config subdir
(`dallas-console` → `configs/site/` + `supervisor/site.conf`, `gw-fill-01` → `configs/filling-line/`,
`gw-pack-01` → `configs/packaging-line/`) and commit. `render/` is gitignored. The Lua under
`configs/lua/` is hand-authored (referenced by the telemetry-processor config) and is not rendered.

The old `config-catalog.tmpl.json` + `render-packaging-catalog` startup substitution is gone:
binding values are resolved at render time, and the packaging catalog is a static rendered file
mounted at the path the bootstrap's catalogSource declares.
