# Sealos release tools

The shared Linux amd64 shell runner uses the kubectl image pinned by digest in
`scripts/ci/sealos-release.sh`. It logs in to ACR before pulling the image and
mounts the GitLab File variable read-only. No cluster credentials are in the image.
The image contains kubectl v1.28.15, matching the Sealos Kubernetes 1.28 server.

To rebuild from an empty temporary directory on a trusted build machine:

```sh
curl -fL -o kubectl https://dl.k8s.io/release/v1.28.15/bin/linux/amd64/kubectl
echo '1f7651ad0b50ef4561aa82e77f3ad06599b5e6b0b2a5fb6c4f474d95a77e41c5  kubectl' | sha256sum -c -
docker build --platform linux/amd64 -f /path/to/GeoAgent/deploy/ci-tools/Dockerfile \
  -t crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent:kubectl-v1.28.15 .
docker push crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent:kubectl-v1.28.15
```

After a rebuild, update the script's immutable digest and verify cluster access.
Do not add the downloaded binary or kubeconfig to Git.
