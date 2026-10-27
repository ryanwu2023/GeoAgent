#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
[[ ${CI_DEBUG_TRACE:-false} == false && ${CI_DEBUG_SERVICES:-false} == false ]] || exit 1
[[ ${CI_COMMIT_BRANCH:-} == main && ${CI_COMMIT_REF_PROTECTED:-} == true ]] || exit 1
: "${GEOAGENT_PROD_KUBECONFIG:?Production file variable required}"
tools_image=crpi-pkfixdfs4f7qepax.cn-shanghai.personal.cr.aliyuncs.com/stocklink-base/geoagent@sha256:18e9a34191812fb74569e8070f6833bbf90adea1da17cdc9d40e0aef427724fb
kubectl() {
  docker run --rm -i --network host --user "$(id -u):$(id -g)" \
    --mount "type=bind,source=$GEOAGENT_PROD_KUBECONFIG,target=/kube/config,readonly" \
    --env KUBECONFIG=/kube/config "$tools_image" --request-timeout=30s "$@"
}
[[ $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}') == https://gzg.sealos.run:6443 ]] || exit 1
namespace=ns-d40dap45
kubectl -n "$namespace" get deployment geoagent -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}{.spec.template.metadata.labels}{"\n"}'
kubectl -n "$namespace" get networkpolicy -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec}{"\n"}{end}'
kubectl -n "$namespace" exec -i deployment/geoagent -c geoagent -- python - < scripts/ci/diagnose_identity.py
