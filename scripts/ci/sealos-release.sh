#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

[[ ${CI_COMMIT_TAG:-} =~ ^geoagent-prod-v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Not a GeoAgent production tag'; exit 1; }
[[ ${CI_COMMIT_REF_PROTECTED:-} == true ]] || { echo 'Production tag must be protected'; exit 1; }
[[ ${CI_DEBUG_TRACE:-false} == false && ${CI_DEBUG_SERVICES:-false} == false ]] || exit 1
git fetch origin main
git merge-base --is-ancestor "$CI_COMMIT_SHA" origin/main || { echo 'Release must be merged into main'; exit 1; }

case ${1:-} in
  verify)
    docker build --build-arg VITE_PICHAT_EMBED_ORIGIN=https://luozi-chat.lhotsetech.com \
      --build-arg VITE_PICHAT_PRODUCT_USER_ID=geoagent-owner \
      -t "geoagent-verify:${CI_JOB_ID}" .
    trap 'docker image rm "geoagent-verify:${CI_JOB_ID}" >/dev/null 2>&1 || true' EXIT
    docker run --rm --entrypoint python "geoagent-verify:${CI_JOB_ID}" -c 'from backend.app import app; assert app.title'
    docker run --rm --mount "type=bind,source=$PWD/tests,target=/app/tests,readonly" \
      --entrypoint python "geoagent-verify:${CI_JOB_ID}" -m pytest -q /app/tests/test_public_origin.py /app/tests/test_sealos_release.py -p no:cacheprovider
    ;;
  release)
    : "${GEOAGENT_PROD_KUBECONFIG:?Set a protected File variable scoped to geoagent-sealos-production}"
    : "${GEOAGENT_PROD_ACR_USER:?Set production registry username}"
    : "${GEOAGENT_PROD_ACR_PASSWORD:?Set masked production registry password}"
    # Install in this job only; the shared shell runner need not have kubectl.
    job_tools=$(mktemp -d)
    trap 'rm -rf -- "$job_tools"' EXIT
    case $(uname -m) in
      x86_64) arch=amd64 ;;
      aarch64) arch=arm64 ;;
      *) echo 'Unsupported runner architecture'; exit 1 ;;
    esac
    # Match the Sealos 1.28 API server, with an official checksum verification.
    kubectl_url="https://dl.k8s.io/release/v1.28.15/bin/linux/$arch/kubectl"
    curl --fail --silent --show-error --location --retry 3 --connect-timeout 15 --max-time 180 -o "$job_tools/kubectl" "$kubectl_url"
    curl --fail --silent --show-error --location --retry 3 --connect-timeout 15 --max-time 60 -o "$job_tools/kubectl.sha256" "$kubectl_url.sha256"
    printf '%s  %s\n' "$(cat "$job_tools/kubectl.sha256")" "$job_tools/kubectl" | sha256sum --check --status
    chmod 700 "$job_tools/kubectl"
    export PATH="$job_tools:$PATH"
    export KUBECONFIG="$GEOAGENT_PROD_KUBECONFIG"
    [[ $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}') == https://gzg.sealos.run:6443 ]] || exit 1
    namespace=ns-d40dap45
    registry=crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com
    repository="$registry/stocklink-base/geoagent"
    image="$repository:${CI_COMMIT_TAG}-${CI_COMMIT_SHA}"
    # Job-scoped Docker credentials must not overwrite the runner's shared login.
    export DOCKER_CONFIG
    DOCKER_CONFIG="$job_tools/docker"
    mkdir "$DOCKER_CONFIG"
    printf '%s' "$GEOAGENT_PROD_ACR_PASSWORD" | docker login "$registry" --username "$GEOAGENT_PROD_ACR_USER" --password-stdin
    docker build --build-arg VITE_PICHAT_EMBED_ORIGIN=https://luozi-chat.lhotsetech.com \
      --build-arg VITE_PICHAT_PRODUCT_USER_ID=geoagent-owner -t "$image" .
    docker push "$image"
    digest=$(docker image inspect "$image" --format '{{index .RepoDigests 0}}')
    [[ "$digest" == "$repository"@sha256:* ]] || exit 1
    previous=$(kubectl -n "$namespace" get deployment geoagent -o jsonpath='{.spec.template.spec.containers[?(@.name=="geoagent")].image}')
    mkdir -p release
    printf 'commit=%s\ntag=%s\nprevious=%s\nimage=%s\n' "$CI_COMMIT_SHA" "$CI_COMMIT_TAG" "$previous" "$digest" > release/geoagent-release.txt
    # Update only the image: preserve PVC, runtime secrets, ingress and other apps.
    kubectl -n "$namespace" set image deployment/geoagent "geoagent=$digest"
    kubectl -n "$namespace" rollout status deployment/geoagent --timeout=300s
    kubectl -n "$namespace" exec deployment/geoagent -c geoagent -- python -c 'import urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000/api/status",timeout=15); assert r.status==200; print("GeoAgent status: HTTP 200")'
    ;;
  *) echo 'Usage: sealos-release.sh verify|release'; exit 2 ;;
esac
