PostgreSQL setup and migration are required before using this deployment: [instructions](POSTGRESQL.md).

# Sealos deployment

Current release (2026-10-28): form login plus collector fixes, image `crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent@sha256:ed4b0d35c58b4a490bb9ace32663e3c41f07acbc62fb6cec65881eb2ca7e57fa`. See [release validation and rollback](LOGIN-AND-COLLECTOR-FIX-20261028.md). The dated entries below describe earlier deployments.

Prepared on 2026-10-08 from main commit `65bd31995bae0f6aaf2612954e61f885ba2f7ce7`.

## Image

Built locally with Docker and pushed to the existing Alibaba Cloud registry:

```text
crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent:65bd319-sealos-20261008
sha256:4d3fe4b667878b6e6fb7b50b03dee7bce1b46cc9f8e9ac9d13eb726fd77c5c9a
```

The manifest pins this digest. Rebuild and update the digest when source or dependencies change.

## Storage and access

- One replica, Recreate updates; do not enable horizontal scaling until collector locking and file storage support multiple hosts.
- 5 GiB ReadWriteOnce PVC mounted at `/app/data` retains collector output, model settings and archives; PostgreSQL is external.
- Container and Service port: 8000. CPU limit: 0.5 core; memory limit: 1 GiB. Verify namespace quota and pricing before applying.
- The deployed manifest reuses the existing `stocklinkes-mcp` ACR pull secret in `ns-d40dap45`. Other namespaces require their own pull secret. Do not commit credentials.
- Public HTTPS: https://geoagentd40d.sealosgzg.site (enabled 2026-10-09). The application serves `/login` and enforces PostgreSQL-backed sessions on business routes. Existing password hashes are mounted read-only from `geoagent-web-auth` into the application container; MCP uses independent Bearer authentication.
- Use `kubectl --kubeconfig <file> -n <namespace> port-forward svc/geoagent 8000:8000`, for diagnostics. Browser login uses the public HTTPS domain because production session cookies require HTTPS.
- `GEOAGENT_ALLOWED_ORIGINS` allows the exact public origin for browser writes. Unknown origins remain forbidden. `sealos-ingress.yaml` permits inbound port 8080 through the namespace's existing default-deny policy; port 8000 stays isolated. Existing egress restrictions remain in effect (live collection requires a separately scoped egress policy).

The public image is `geoagent:65bd319-public-20261009` in the same ACR repository, pinned by the deployment to `sha256:b3f7d99efa8b7b1994772fc70fc83e37f6e1a3520b1efb32871866784fea211c`.

Custom domain bound on 2026-10-09: `geoagent.lhotsetech.com`, using `deploy/sealos-custom-domain.yaml` and the existing wildcard certificate Secret `pi-chat-lhotsetech-tls`. The certificate covers `*.lhotsetech.com` and expires on 2026-10-29. Public DNS now confirms `geoagent CNAME geoagentd40d.sealosgzg.site`. TLS and authentication were verified against the public DNS address with normal certificate verification enabled: anonymous 401, authenticated status succeeds with the existing working snapshot. This workstation's normal resolver/proxy path initially failed TLS after the DNS change, while the resolved public address worked. The original Sealos address remains available. Both exact HTTPS origins are allowed for browser writes, and access credentials are unchanged.
Create the versioned `geoagent-gateway-login-v1` ConfigMap using `--from-file=default.conf=deploy/gateway.conf`; ensure `geoagent-web-auth` exists and `GEO_WEB_AUTH_ENABLED=true` is set. Switch image, authentication mount, readiness probe and gateway reference together. Never remove Basic auth from a running old gateway before the matching session-protected app is deployed.

After verifying the target namespace and creating the pull secret:

```text
kubectl --kubeconfig <file> -n <namespace> apply -f deploy/sealos.yaml
kubectl --kubeconfig <file> -n <namespace> rollout status deployment/geoagent
kubectl --kubeconfig <file> -n <namespace> get pods,pvc,svc -l cloud.sealos.io/app-deploy-manager=geoagent
```

Deployed on 2026-10-08 to Guangzhou (`https://gzg.sealos.run:6443`), namespace `ns-d40dap45`. Server-side admission passed, deployment rollout succeeded, and the 5 GiB PVC is Bound using `openebs-lvmpv`. Existing applications were not modified. Pod Security emits a non-blocking warning because the current image runs as root; privilege escalation is disabled and Linux capabilities are dropped.

## Initial data and optional services

The image contains repository demo inputs, not the user's local database or live collector data. A fresh PVC starts with no snapshots. The existing app falls back to demo inputs until real collector output exists. Import and explicitly select a working snapshot before using snapshot-dependent features. Run real collection before treating the data as current.

PiChat/LLM credentials and local translation models are not bundled. Configure optional services through Sealos secrets/environment variables. This manifest starts the web/API process; it does not install a recurring collector scheduler or expose the separate MCP server.

## PiChat embed configuration (deployed 2026-10-09)

The frontend origin is baked into the Vite build. Build with
`--build-arg VITE_PICHAT_EMBED_ORIGIN=https://luozi-chat.lhotsetech.com`
and `--build-arg VITE_PICHAT_PRODUCT_USER_ID=geoagent-owner`.
The agent defaults to `geo-research-assistant`.

Backend runtime settings must use `PICHAT_EMBED_BASE_URL=https://luozi-chat.lhotsetech.com`,
`PICHAT_EMBED_ENTRY=dock`, and `PICHAT_EMBED_PRODUCT_USER_ID=geoagent-owner`.
Supply `PICHAT_EMBED_APP_KEY` and `PICHAT_EMBED_PHONE` through a Kubernetes Secret.
The app key and owner-provided phone are saved in Secret `geoagent-pichat-embed`, referenced by the running deployment.
The embed-only key is not a `PICHAT_API_KEY` for direct chat calls.
Apply `sealos-pichat-egress.yaml` to permit DNS and HTTPS to the current PiChat public gateway addresses. Refresh the address list if PiChat DNS changes.
Use the approved parent origin `https://geoagent.lhotsetech.com` for the embedded panel.

Image `geoagent:65bd319-pichat-20261009` is pinned in `sealos.yaml` to
`sha256:ec4f28af61d48c81ba492154de35e6371d7c9b7c63dc1d6522bb810857898595`.
TypeScript/Vite and Docker build succeeded. Deployment rollout succeeded; both pod-local and authenticated public identity requests returned HTTP 200 with a token for `geo-research-assistant`. Public status also returned HTTP 200. Tokens were not logged.
Chrome verification: the public GeoAgent page's intelligent Q&A panel rendered the embedded GeoAgent assistant welcome screen, message input and model selector after authentication. No test chat was sent; model responses and MCP data queries were not validated in this configuration update.

## Previous validation and known findings

- Docker multi-stage build and TypeScript/Vite production build passed.
- Container `/` and `/api/status` returned HTTP 200.
- A demo snapshot written to a Docker volume survived container restart.
- Live Sealos verification: `/` and `/api/status` returned HTTP 200; an isolated SQLite probe survived deployment restart/Pod replacement and was then removed. A local-only kubectl tunnel was opened at http://127.0.0.1:8000 and verified. It must be restarted after a computer restart or connection loss.
- On an empty database, the existing MCP integration test fails because it expects an existing snapshot. Importing a demo snapshot and selecting it as the working snapshot satisfies its data prerequisites; the MCP test module then passed (8 passed, 1 skipped).
- With those explicit test-data prerequisites, the final full suite passed: 152 passed, 1 skipped (one upstream Starlette deprecation warning).
- Public-access update: 154 passed, 1 skipped in the new image. External HTTPS checks: anonymous requests 401, authenticated homepage/status 200, configured-origin invalid write 422 (reaches validation), untrusted-origin write 403. Existing demo snapshot is retained. No real-data collection or model configuration was added.
- npm audit reports MapLibre GL JS sanitizer bypass (critical, GHSA-jrc7-96c5-q579) and source-map-js build-tool DoS (high, GHSA-68fv-2mgg-jv7q). The current map uses local GeoJSON and a fixed attribution string, with no `Popup.setHTML` use found; source-map-js is in the build stage. These findings remain open; the image must not be represented as vulnerability-free. Upgrading MapLibre requires a major-version compatibility check.
