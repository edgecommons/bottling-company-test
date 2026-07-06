# ============================================================================
# site image — the dallas-site node: a local EMQX bus + the edge-console, run as supervised
# processes in one container.
#
# The console build is identical to system-test's console.Dockerfile: build the sibling
# edgecommons TS lib dist first (dropping in the type-only @edgecommons/streamlog-node stub AFTER
# npm install and BEFORE tsc, so the never-used streaming import resolves), then link the
# sibling into edge-console and build protocol -> server -> ui (ui/dist). As of edge-console
# commit ae94d31 the Node server serves its OWN built UI on the WS port when
# component.global.console.ws.webRoot is set — so there is NO nginx/Vite sidecar.
#
# BUILD CONTEXT = the edgecommons umbrella root (compose sets build.context: ../../..). The
# per-Dockerfile site.Dockerfile.dockerignore carries the same rules as the edge-node one
# (incl. the critical !**/src/**/target/ re-include the TS build needs).
# ============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — build the edgecommons TS lib dist + the edge-console (protocol/server/ui).
# ---------------------------------------------------------------------------
FROM node:22 AS console-build
WORKDIR /build

# Build the sibling edgecommons TS lib (produces libs/ts/dist + its own node_modules). The native
# streaming addon @edgecommons/streamlog-node is an unpublished OPTIONAL dep. In the monorepo it is
# declared as workspace:*, which plain npm cannot resolve in this image because only core/libs/ts is
# copied. The console never uses streaming, so point that optional dependency at the existing
# type-only stub before npm install. The edgecommons repo on disk is untouched; the package.json
# mutation lives only in-image.
COPY core/libs/ts core/libs/ts
COPY bottling-company-test/dockerfiles/streamlog-node-stub /tmp/streamlog-node-stub
RUN cd core/libs/ts \
    && node -e "const fs=require('fs'); const p=require('./package.json'); p.optionalDependencies=p.optionalDependencies||{}; p.optionalDependencies['@edgecommons/streamlog-node']='file:/tmp/streamlog-node-stub'; fs.writeFileSync('package.json', JSON.stringify(p,null,2)+'\n');" \
    && npm install \
    && npm run build

# Link the sibling, install workspaces, build protocol -> server -> ui (ui/dist). edge-console's
# link:lib generates a gitignored stub re-exporting ../../../core/libs/ts/dist, so the
# sibling MUST stay a built sibling dir of edge-console — the whole /build tree is preserved
# into the runtime stage below to keep that layout intact.
COPY edge-console edge-console
WORKDIR /build/edge-console
RUN npm run link:lib && npm install && npm run build

# ---------------------------------------------------------------------------
# Stage 2 — runtime. Base = node:22-slim (Debian Bookworm) for the console server; add EMQX
# (official apt repo), supervisord and bash (wait-for-tcp gate). Copy the whole build tree so
# edge-console/ and edgecommons/ stay siblings and the server can serve ui/dist.
# ---------------------------------------------------------------------------
FROM node:22-slim AS runtime
ARG EMQX_VERSION=5.8.2

# EMQX version pin: see the note in edge-node.Dockerfile — if "=${EMQX_VERSION}" is not found,
# check `apt-cache madison emqx` and adjust, or drop the pin for the repo's latest 5.x.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      supervisor bash curl ca-certificates gnupg \
 && curl -fsSL https://assets.emqx.com/scripts/install-emqx-deb.sh | bash \
 && apt-get install -y --no-install-recommends "emqx=${EMQX_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

# Built console tree (server dist + ui/dist + the sibling edgecommons dist it links to).
COPY --from=console-build /build /app

# wait-for-tcp readiness gate (the console waits for the local EMQX before connecting).
COPY bottling-company-test/dockerfiles/bin/ /usr/local/bin/
RUN chmod +x /usr/local/bin/wait-for-tcp /usr/local/bin/render-opcua-config /usr/local/bin/render-modbus-config

# supervisord is PID 1; site.conf is bind-mounted by compose at /etc/supervisor/supervisord.conf.
ENTRYPOINT ["supervisord"]
CMD ["-n", "-c", "/etc/supervisor/supervisord.conf"]
