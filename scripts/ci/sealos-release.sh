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
    docker build --provenance=false --sbom=false --build-arg VITE_PICHAT_EMBED_ORIGIN=https://luozi-chat.lhotsetech.com \
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
    # Credentials and tools are isolated from the shared runner's configuration.
    job_tools=$(mktemp -d)
    trap 'rm -rf -- "$job_tools"' EXIT
    namespace=ns-d40dap45
    registry=crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com
    repository="$registry/stocklink-base/geoagent"
    image="$repository:${CI_COMMIT_TAG}-${CI_COMMIT_SHA}"
    # Job-scoped Docker credentials must not overwrite the runner's shared login.
    export DOCKER_CONFIG
    DOCKER_CONFIG="$job_tools/docker"
    mkdir "$DOCKER_CONFIG"
    printf '%s' "$GEOAGENT_PROD_ACR_PASSWORD" | docker login "$registry" --username "$GEOAGENT_PROD_ACR_USER" --password-stdin
    # Verified kubectl v1.28.15, mirrored to ACR; no external downloads on runner.
    tools_image="$repository@sha256:18e9a34191812fb74569e8070f6833bbf90adea1da17cdc9d40e0aef427724fb"
    docker pull "$tools_image"
    kubectl() {
      docker run --rm --network host --user "$(id -u):$(id -g)" \
        --mount "type=bind,source=$GEOAGENT_PROD_KUBECONFIG,target=/kube/config,readonly" \
        --env KUBECONFIG=/kube/config "$tools_image" "$@"
    }
    [[ $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}') == https://gzg.sealos.run:6443 ]] || exit 1
    kubectl --request-timeout=30s -n "$namespace" get deployment geoagent -o name
    # ACR personal registry rejects the runner's OCI attestation artifact format.
    docker build --provenance=false --sbom=false --build-arg VITE_PICHAT_EMBED_ORIGIN=https://luozi-chat.lhotsetech.com \
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
