#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

[[ ${CI_DEBUG_TRACE:-false} == false && ${CI_DEBUG_SERVICES:-false} == false ]] || exit 1
action=${1:-}
production=false
[[ ${CI_COMMIT_TAG:-} =~ ^geoagent-prod-v[0-9]+\.[0-9]+\.[0-9]+$ ]] && production=true
if [[ $action != verify || $production == true ]]; then
  [[ $production == true ]] || { echo 'Not a GeoAgent production tag'; exit 1; }
  [[ ${CI_COMMIT_REF_PROTECTED:-} == true ]] || { echo 'Production tag must be protected'; exit 1; }
  git fetch origin main
  git merge-base --is-ancestor "$CI_COMMIT_SHA" origin/main || { echo 'Release must be merged into main'; exit 1; }
fi
[[ ${CI_COMMIT_SHA:-} =~ ^[a-f0-9]{40}$ && ${CI_PIPELINE_ID:-} =~ ^[0-9]+$ ]] || exit 1
mkdir -p release
candidate="geoagent-ci:${CI_PIPELINE_ID}-${CI_COMMIT_SHA}"
source "$(dirname "$0")/release-policy.sh"

case ${1:-} in
  verify)
    docker build --provenance=false --sbom=false --build-arg VITE_PICHAT_EMBED_ORIGIN=https://luozi-chat.lhotsetech.com \
      --build-arg VITE_PICHAT_PRODUCT_USER_ID=geoagent-owner \
      -t "$candidate" .
    trap 'docker image rm "$candidate" >/dev/null 2>&1 || true' EXIT
    docker run --rm --user "$(id -u):$(id -g)" --env GEO_DATA_DIR=/tmp/geoagent-ci-data \
      --env COLLECTOR_DATA_ROOT=/tmp/geoagent-ci-data/collector-output --env HOME=/tmp \
      --mount "type=bind,source=$PWD/release,target=/reports" \
      --mount "type=bind,source=$PWD/tests,target=/app/tests,readonly" \
      --entrypoint python "$candidate" -m scripts.ci.verify
    # Export the image that passed tests. Deploy never rebuilds it.
    if [[ -n ${CI_COMMIT_TAG:-} ]]; then
      docker save "$candidate" | gzip > release/candidate.tar.gz
      (cd release && sha256sum candidate.tar.gz > candidate.sha256)
      docker image inspect "$candidate" --format '{{.Id}}' > release/candidate.id
    fi
    ;;
  deploy|rollback)
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
      docker run --rm -i --network host --user "$(id -u):$(id -g)" \
        --mount "type=bind,source=$GEOAGENT_PROD_KUBECONFIG,target=/kube/config,readonly" \
        --env KUBECONFIG=/kube/config "$tools_image" "$@"
    }
    [[ $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}') == https://gzg.sealos.run:6443 ]] || exit 1
    kubectl --request-timeout=30s -n "$namespace" get deployment geoagent -o name
    record="geoagent-release-${CI_PIPELINE_ID}"
    current_owner=$(kubectl -n "$namespace" get deployment geoagent -o jsonpath='{.metadata.annotations.geoagent-release-owner}')
    sequence=$(kubectl -n "$namespace" get deployment geoagent -o jsonpath='{.metadata.annotations.geoagent-release-sequence}')
    revision=$(kubectl -n "$namespace" get deployment geoagent -o jsonpath='{.metadata.resourceVersion}')
    # Container order may change when sidecars are added; resolve the application index.
    names=$(kubectl -n "$namespace" get deployment geoagent -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{"\n"}{end}')
    index=0
    while IFS= read -r name; do [[ $name == geoagent ]] && break; index=$((index+1)); done <<< "$names"
    [[ $name == geoagent ]] || exit 1
    previous=$(kubectl -n "$namespace" get deployment geoagent -o jsonpath='{.spec.template.spec.containers[?(@.name=="geoagent")].image}')
    if [[ $action == deploy ]]; then
      assert_forward_release "$CI_PIPELINE_ID" "${sequence:-0}"
      [[ -z $(kubectl -n "$namespace" get configmap "$record" --ignore-not-found -o name) ]] || {
        echo 'Release record already exists; inspect it before retrying. Use rollback for an applied release.'; exit 1;
      }
      (cd release && sha256sum --check candidate.sha256)
      docker load -i release/candidate.tar.gz
      [[ $(docker image inspect "$candidate" --format '{{.Id}}') == "$(cat release/candidate.id)" ]] || exit 1
      docker tag "$candidate" "$image"
      docker push "$image"
      digest=$(docker image inspect "$image" --format '{{index .RepoDigests 0}}')
      [[ $digest == "$repository"@sha256:* ]] || exit 1
      backup_path=$(kubectl -n "$namespace" exec -i deployment/geoagent -c geoagent -- python - "$CI_PIPELINE_ID" < scripts/ci/backup.py)
      kubectl -n "$namespace" create configmap "$record" \
        --from-literal="previous=$previous" --from-literal="image=$digest" \
        --from-literal="commit=$CI_COMMIT_SHA" --from-literal="pipeline=$CI_PIPELINE_ID" \
        --from-literal="backup=$backup_path"
      next_owner=$CI_PIPELINE_ID
      next_sequence=$CI_PIPELINE_ID
    else
      digest=$(kubectl -n "$namespace" get configmap "$record" -o jsonpath='{.data.previous}')
      deployed_image=$(kubectl -n "$namespace" get configmap "$record" -o jsonpath='{.data.image}')
      assert_rollback_owner "$CI_PIPELINE_ID" "$current_owner" "$deployed_image" "$previous"
      next_owner="rollback-$CI_PIPELINE_ID"
      next_sequence=$sequence
    fi
    [[ $digest == "$repository"@sha256:* && ${digest##*@sha256:} =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid target image'; exit 1; }
    printf 'action=%s\ncommit=%s\ntag=%s\npipeline=%s\nprevious=%s\nimage=%s\n' \
      "$action" "$CI_COMMIT_SHA" "$CI_COMMIT_TAG" "$CI_PIPELINE_ID" "$previous" "$digest" > release/geoagent-release.txt
    # CAS prevents an out-of-band deployment/config change being overwritten.
    annotations=$(kubectl -n "$namespace" get deployment geoagent -o jsonpath='{.metadata.annotations}')
    init_annotations=''
    [[ -n $annotations ]] || init_annotations='{"op":"add","path":"/metadata/annotations","value":{}},'
    patch="[{\"op\":\"test\",\"path\":\"/metadata/resourceVersion\",\"value\":\"$revision\"},${init_annotations}{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/$index/image\",\"value\":\"$digest\"},{\"op\":\"add\",\"path\":\"/metadata/annotations/geoagent-release-owner\",\"value\":\"$next_owner\"},{\"op\":\"add\",\"path\":\"/metadata/annotations/geoagent-release-sequence\",\"value\":\"$next_sequence\"}]"
    kubectl -n "$namespace" patch deployment geoagent --type=json -p "$patch"
    status=failed
    if kubectl -n "$namespace" rollout status deployment/geoagent --timeout=300s &&
       kubectl -n "$namespace" exec deployment/geoagent -c geoagent -- python -c 'import json,urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000/api/status",timeout=15); assert r.status==200; assert "snapshots" in json.load(r); print("GeoAgent status: HTTP 200")' &&
       [[ $(curl --silent --show-error --max-time 30 -o /dev/null -w '%{http_code}' https://geoagent.lhotsetech.com/api/status) == 401 ]]; then
      status=passed
    fi
    printf 'status=%s\n' "$status" >> release/geoagent-release.txt
    kubectl -n "$namespace" annotate configmap "$record" "geoagent-$action-status=$status" --overwrite
    [[ $status == passed ]] || { echo 'Health check failed. Inspect the release; manual rollback is available. Database was not restored.'; exit 1; }
    ;;
  *) echo 'Usage: sealos-release.sh verify|deploy|rollback'; exit 2 ;;
esac
