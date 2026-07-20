# ============================================================================
# edge-node image — ONE image, both production-line edge devices.
#
# Collapses the per-component containers of ../system-test into a single "edge gateway"
# image that bundles EVERYTHING an edge device runs: a local EMQX broker, the field sims,
# all the edgecommons components, and supervisord to run them as processes (bare-metal style).
# The filling line and the packaging line run the SAME image with DIFFERENT supervisord
# confs + configs (bind-mounted by compose): filling starts EMQX + 2 sims + 5 components;
# packaging starts EMQX + 2 adapters + the bridge (no sims / telemetry / file-replicator).
#
# BUILD CONTEXT = the edgecommons umbrella root (compose sets build.context: ../../..) so
# each stage can COPY the UNPUBLISHED sibling core/libs/* next to each component repo,
# exactly as system-test does. The per-Dockerfile .dockerignore
# (edge-node.Dockerfile.dockerignore) keeps the multi-GB target/ node_modules/ .git/ trees
# out of the context and re-includes the TS lib's src-nested `target/` source dirs.
# ============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Java (OPC UA adapter, Eclipse Milo). mvn-install the sibling edgecommons Java lib
# into ~/.m2 (shadows the published artifact), then package the shaded adapter jar against it.
# ---------------------------------------------------------------------------
FROM maven:3.9-eclipse-temurin-25 AS java-build
WORKDIR /build
COPY core/proto core/proto
COPY core/libs/java core/libs/java
RUN cd core/libs/java && mvn -q -DskipTests -Dmaven.test.skip=true install
COPY opcua-adapter opcua-adapter
RUN cd opcua-adapter && mvn -q -DskipTests -Dmaven.test.skip=true package

# ---------------------------------------------------------------------------
# Stage 2 — Rust (telemetry-processor, file-replicator, uns-bridge, config-component). The sibling edgecommons
# crates are copied next to each component, and each copied Cargo.toml is rewritten in-image
# to depend on ../core/libs/rust (edgecommons's own path-dep ../rust-streamlog also resolves). Only the
# features this harness needs are built — no kafka/kinesis/greengrass, so no heavy C builds
# beyond mlua (scripting-lua) + bundled rusqlite, both provided by rust:bookworm's toolchain.
# ---------------------------------------------------------------------------
FROM rust:1-bookworm AS rust-build
WORKDIR /build
COPY core/proto core/proto
COPY core/libs/rust core/libs/rust
COPY core/libs/rust-streamlog core/libs/rust-streamlog
COPY bottling-company-test/dockerfiles/cargo-sibling-patch.toml /tmp/cargo-sibling-patch.toml
COPY telemetry-processor telemetry-processor
COPY file-replicator file-replicator
COPY uns-bridge uns-bridge
COPY config-component config-component
RUN mkdir -p telemetry-processor/.cargo file-replicator/.cargo uns-bridge/.cargo config-component/.cargo \
 && cp /tmp/cargo-sibling-patch.toml telemetry-processor/.cargo/config.toml \
 && cp /tmp/cargo-sibling-patch.toml file-replicator/.cargo/config.toml \
 && cp /tmp/cargo-sibling-patch.toml uns-bridge/.cargo/config.toml \
 && cp /tmp/cargo-sibling-patch.toml config-component/.cargo/config.toml
RUN for crate in telemetry-processor file-replicator uns-bridge config-component; do \
      sed -i 's#^edgecommons = { git = "https://github.com/edgecommons/edgecommons.git".*#edgecommons = { path = "../core/libs/rust", default-features = false }#' "$crate/Cargo.toml"; \
    done
RUN cd telemetry-processor && cargo build --release --no-default-features \
      --features "standalone,streaming,streaming-file-parquet,scripting-lua"
RUN cd file-replicator && cargo build --release --no-default-features --features standalone
RUN cd uns-bridge && cargo build --release
RUN cd config-component && cargo build --release

# ---------------------------------------------------------------------------
# Stage 3 — runtime. Base = Temurin 25 JRE on Ubuntu Noble (glibc 2.39). The Rust binaries
# are built on Debian Bookworm (glibc 2.36); a NEWER runtime glibc runs OLDER-built binaries,
# so Noble is a safe runtime for them (do not drop to a jammy/2.35 base). This base gives us
# the JRE for the OPC UA adapter for free; we add Python (venv), EMQX, supervisord and copy in
# the built artifacts + the two field sims.
# ---------------------------------------------------------------------------
FROM eclipse-temurin:25-jre-noble AS runtime
ARG EMQX_VERSION=5.8.2

# System deps: python venv toolchain, EMQX (official apt repo), supervisord, bash (for the
# wait-for-tcp /dev/tcp gate), curl/ca-certs/gnupg (repo add + Rust TLS roots).
#
# EMQX version pin: some packagecloud repos expose the plain "5.8.2" string, others append a
# distro suffix (e.g. "5.8.2-1~ubuntu24.04"). If this exact pin is not found, run
# `apt-cache madison emqx` in the repo and set --build-arg EMQX_VERSION=<string>, or drop the
# "=${EMQX_VERSION}" to take the repo's latest 5.x. (system-test pinned emqx/emqx:5.8.2.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      supervisor bash curl ca-certificates gnupg \
 && curl -fsSL https://assets.emqx.com/scripts/install-emqx-deb.sh | bash \
 && apt-get install -y --no-install-recommends "emqx=${EMQX_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

# Python venv: the sibling edgecommons Python lib + the Modbus adapter runtime dep (pymodbus)
# + the OPC UA sim dep (asyncua). One shared venv serves the adapter AND both sims.
COPY core/libs/python /src/edgecommons-python
RUN python3 -m venv /opt/pyenv \
 && /opt/pyenv/bin/pip install --no-cache-dir \
      /src/edgecommons-python "pymodbus>=3.6" "asyncua>=1.0"

# Rust binaries (from stage 2).
COPY --from=rust-build /build/telemetry-processor/target/release/telemetry-processor /usr/local/bin/telemetry-processor
COPY --from=rust-build /build/file-replicator/target/release/file-replicator /usr/local/bin/file-replicator
COPY --from=rust-build /build/uns-bridge/target/release/uns-bridge /usr/local/bin/uns-bridge
COPY --from=rust-build /build/config-component/target/release/config-component /usr/local/bin/config-component

# Java OPC UA adapter jar (from stage 1).
COPY --from=java-build /build/opcua-adapter/target/opcua-adapter-1.0.0.jar /app/opcua/app.jar

# Python Modbus adapter source (runs as `python main.py`, importing the local modbus_adapter).
COPY modbus-adapter/main.py /app/modbus/main.py
COPY modbus-adapter/modbus_adapter /app/modbus/modbus_adapter

# Field sims — used ONLY by the filling line (packaging never starts them). Because the sim
# and the adapter now share ONE container, the adapter reaches the OPC UA sim over loopback
# (opc.tcp://localhost:4840) — the cross-container endpoint-rewrite the system-test image did
# is NO LONGER NEEDED, so these scripts are copied verbatim.
COPY opcua-adapter/validation/opcua_sim_server.py /opt/sims/opcua_sim_server.py
COPY modbus-adapter/validation/modbus_sim_server.py /opt/sims/modbus_sim_server.py

# Launcher helpers: wait-for-tcp (the EMQX/sim readiness gate) + the packaging template
# renderers (host:port / endpoint / creds -> /run/config, kept out of the supervisord command
# line because '%' is a supervisord sigil).
COPY bottling-company-test/dockerfiles/bin/ /usr/local/bin/
RUN chmod +x /usr/local/bin/wait-for-tcp /usr/local/bin/render-opcua-config /usr/local/bin/render-modbus-config /usr/local/bin/render-packaging-catalog

# Pipeline scratch dirs (telemetry writes /out/archive; file-replicator reads it and archives
# to /out/_archived — both in THIS container now, no shared volume needed) + the render dir +
# the /mnt/replicated egress mountpoint (compose bind-mounts the host replicated-output here).
RUN mkdir -p /out/archive /out/.stream-buffer/archive /out/_archived /mnt/replicated /run/config

# supervisord is PID 1; the actual conf (filling-line.conf | packaging-line.conf) is
# bind-mounted by compose at /etc/supervisor/supervisord.conf.
ENTRYPOINT ["supervisord"]
CMD ["-n", "-c", "/etc/supervisor/supervisord.conf"]
