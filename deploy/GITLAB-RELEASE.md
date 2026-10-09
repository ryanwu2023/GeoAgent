# GeoAgent Sealos production releases

Production tag: `geoagent-prod-v1.2.3` (three numeric version components).
Only protected tags matching that exact format create this pipeline. Test tags
such as `geoagent-test-v1.2.3`, `stocklink-prod-*`, branches, and unprotected tags
do not trigger production jobs. GeoAgent currently has no test deployment lane;
this configuration does not create one or change the pi-chat-server project.

The pipeline follows pi-chat-server's dedicated `pichat-sealos-release` shell
runner pattern. The runner must have Docker, Git, and kubectl, registry access,
and network access to the Sealos API. Assign an appropriate protected runner to
this project before releasing. Do not attach the isolated MR runner or assume
another project's runner is automatically available here.

In GitLab, protect `geoagent-prod-*` tags (Maintainers create them) and configure
the following protected variables, scoped to `geoagent-sealos-production`:

| Variable | Type | Purpose |
| --- | --- | --- |
| `GEOAGENT_PROD_KUBECONFIG` | File | Production cluster credential; prefer a dedicated account limited to GeoAgent deployment and probes |
| `GEOAGENT_PROD_ACR_USER` | Variable | ACR username |
| `GEOAGENT_PROD_ACR_PASSWORD` | Masked variable | ACR push password |

Keep expansion disabled for secret values. Never commit kubeconfig, registry
credentials, PiChat keys, web passwords, or phone numbers. Existing runtime
Secrets and SQLite PVC are reused; this pipeline does not reinitialize data.

After merging the release configuration into main:

```sh
git switch main
git pull --ff-only
git tag -a geoagent-prod-v1.0.0 -m 'GeoAgent Sealos production 1.0.0'
git push origin geoagent-prod-v1.0.0
```

Creating the protected tag automatically builds and releases. The script rejects
commits outside main, checks the cluster address, and serializes the complete
build/push/deploy/probe sequence with `geoagent-sealos-production` resource group.
The image uses tag plus full commit SHA and deployment uses its registry digest.
`release/geoagent-release.txt` records the previous and new image for 90 days.
Failed rollouts stay failed for diagnosis; rollback explicitly to the recorded
previous digest with `kubectl set image`, then check rollout status.

The verify job checks the production build, app import, origin protection and release isolation tests. The release job checks
pod readiness and the API status endpoint. These checks do not validate PiChat
MCP connectivity or live data collection.
