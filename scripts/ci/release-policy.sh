#!/usr/bin/env bash
# Shared by deployment and rollback; callers supply observed live state.
assert_forward_release() {
  local incoming=$1 deployed=${2:-0}
  [[ $incoming =~ ^[0-9]+$ && $deployed =~ ^[0-9]+$ && $incoming -gt $deployed ]] || {
    echo 'Refusing an old or already-applied pipeline'; return 1;
  }
}
assert_rollback_owner() {
  local pipeline=$1 owner=$2 expected=$3 current=$4
  [[ $pipeline == "$owner" && $expected == "$current" ]] || {
    echo 'Rollback refused: a different release now owns this deployment'; return 1;
  }
}
