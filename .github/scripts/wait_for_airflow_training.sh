#!/usr/bin/env bash
# Keep an image rollout from terminating LocalExecutor training processes.
set -euo pipefail

NS="${AIRFLOW_NAMESPACE:-dealight}"
SELECTOR="${AIRFLOW_SCHEDULER_SELECTOR:-app=airflow-scheduler}"
TIMEOUT_SECONDS="${TRAINING_DRAIN_TIMEOUT_SECONDS:-2700}"
POLL_SECONDS="${TRAINING_DRAIN_POLL_SECONDS:-15}"
elapsed=0

while true; do
  pod="$(
    kubectl -n "$NS" get pods \
      --selector "$SELECTOR" \
      --field-selector status.phase=Running \
      --sort-by=.metadata.creationTimestamp \
      -o name | tail -1
  )"
  if [ -z "$pod" ]; then
    echo "::error::No running Airflow scheduler pod found"
    exit 1
  fi
  running="$(
    kubectl -n "$NS" exec "$pod" -- python -c '
from airflow.models import DagRun
from airflow.utils.session import create_session
with create_session() as session:
    count = session.query(DagRun).filter(
        DagRun.dag_id.like("train\\_%", escape="\\"),
        DagRun.state.in_(("queued", "running")),
    ).count()
print(count)
'
  )"
  running="${running##*$'\n'}"
  if [ "$running" = "0" ]; then
    echo "No queued or running training DAGs; rollout can proceed."
    exit 0
  fi
  if [ "$elapsed" -ge "$TIMEOUT_SECONDS" ]; then
    echo "::error::${running} training DAG run(s) still active after ${TIMEOUT_SECONDS}s"
    exit 1
  fi
  echo "Waiting for ${running} training DAG run(s) (${elapsed}s/${TIMEOUT_SECONDS}s)"
  sleep "$POLL_SECONDS"
  elapsed=$((elapsed + POLL_SECONDS))
done
