# ============================================================================
# site image — the dallas-site node: a local EMQX bus + the Rust edge-console gateway,
# run as supervised processes in one container.
#
# The console UI build is identical to system-test's console.Dockerfile: build the sibling
# edgecommons TS lib dist first (dropping in the type-only @edgecommons/streamlog-node stub AFTER
# npm install and BEFORE tsc, so the never-used streaming import resolves), then link the
# sibling into edge-console and build protocol -> ui (ui/dist). The official console process is
# the Rust edge-console-gateway binary, which serves that built UI on the WS port when
# component.global.console.ws.webRoot is set. There is NO nginx/Vite sidecar.
#
# BUILD CONTEXT = the edgecommons umbrella root (compose sets build.context: ../../..). The
# per-Dockerfile site.Dockerfile.dockerignore carries the same rules as the edge-node one
# (incl. the critical !**/src/**/target/ re-include the TS build needs).
# ============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — build the edge-console protocol/ui assets (ui/dist).
# ---------------------------------------------------------------------------
FROM node:22 AS console-build
WORKDIR /build

# edge-console no longer depends on the edgecommons TS library — the core-TS-lib link machinery
# (`link:lib` + the re-export stub) was removed upstream, so the UI build is self-contained: install
# the workspace deps and build protocol -> ui. The gateway (Stage 2) is the only piece that consumes
# the Rust core.
COPY edge-console edge-console
WORKDIR /build/edge-console
RUN npm install && npm run build -w protocol && npm run build -w ui

# ---------------------------------------------------------------------------
# Stage 2 — build the Rust edge-console gateway.
# ---------------------------------------------------------------------------
FROM rust:1-bookworm AS console-gateway-build
WORKDIR /build
COPY core/proto core/proto
COPY core/libs/rust core/libs/rust
COPY core/libs/rust-streamlog core/libs/rust-streamlog
COPY edge-console edge-console
RUN cd edge-console \
 && mkdir -p local \
 && ln -s ../../core/libs/rust local/edgecommons-rust \
 && ln -s ../../core/libs/rust-streamlog local/rust-streamlog \
 && ln -s ../core/proto proto \
 && cargo build -p edge-console-gateway --release

# ---------------------------------------------------------------------------
# Stage 3 — build the Rust ConfigComponent used by the site node.
# ---------------------------------------------------------------------------
FROM rust:1-bookworm AS config-build
WORKDIR /build
COPY core/proto core/proto
COPY core/libs/rust core/libs/rust
COPY core/libs/rust-streamlog core/libs/rust-streamlog
COPY bottling-company-test/dockerfiles/cargo-sibling-patch.toml /tmp/cargo-sibling-patch.toml
COPY config-component config-component
RUN mkdir -p config-component/.cargo \
 && cp /tmp/cargo-sibling-patch.toml config-component/.cargo/config.toml \
 && sed -i 's#^edgecommons = { git = "https://github.com/edgecommons/edgecommons.git".*#edgecommons = { path = "../core/libs/rust", default-features = false }#' config-component/Cargo.toml
RUN cd config-component && cargo build --release

# ---------------------------------------------------------------------------
# Stage 4 — runtime. Base = Debian Bookworm; add EMQX
# (official apt repo), supervisord and bash (wait-for-tcp gate). Copy the whole build tree so
# edge-console-gateway can serve ui/dist.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS runtime
ARG EMQX_VERSION=5.8.2

# EMQX version pin: see the note in edge-node.Dockerfile — if "=${EMQX_VERSION}" is not found,
# check `apt-cache madison emqx` and adjust, or drop the pin for the repo's latest 5.x.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      supervisor bash curl ca-certificates gnupg procps \
 && curl -fsSL https://assets.emqx.com/scripts/install-emqx-deb.sh | bash \
 && apt-get install -y --no-install-recommends "emqx=${EMQX_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

# Built console UI + Rust gateway.
COPY --from=console-build /build/edge-console/ui/dist /app/edge-console/ui/dist
COPY --from=console-gateway-build /build/edge-console/target/release/edge-console-gateway /usr/local/bin/edge-console-gateway
COPY --from=config-build /build/config-component/target/release/config-component /usr/local/bin/config-component

# wait-for-tcp readiness gate (the console waits for the local EMQX before connecting).
COPY bottling-company-test/dockerfiles/bin/ /usr/local/bin/
RUN chmod +x /usr/local/bin/wait-for-tcp /usr/local/bin/render-opcua-config /usr/local/bin/render-modbus-config /usr/local/bin/render-packaging-catalog

# supervisord is PID 1; site.conf is bind-mounted by compose at /etc/supervisor/supervisord.conf.
ENTRYPOINT ["supervisord"]
CMD ["-n", "-c", "/etc/supervisor/supervisord.conf"]
