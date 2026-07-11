#!/usr/bin/env bash
# Launch/wait Kaniko build Jobs in ns ci-builds (runner has RBAC from Task 9).
# Usage: kaniko_build.sh launch <image> <dockerfile> <context-sub-path> [extra-arg...]
#        kaniko_build.sh wait <image>
set -euo pipefail

MODE="${1:?launch|wait}" IMAGE="${2:?image name}"
NS=ci-builds
JOB="kaniko-${IMAGE}-${GITHUB_SHA:0:7}"

if [ "$MODE" = launch ]; then
  DOCKERFILE="${3:?dockerfile}" SUBPATH="${4:-}"
  shift 4 2>/dev/null || shift 3
  ARGS="            - --context=${GIT_CONTEXT}#refs/heads/main#${GITHUB_SHA}
            - --dockerfile=${DOCKERFILE}
            - --destination=${AR}/${IMAGE}:${GITHUB_SHA}
            - --cache=true"
  if [ -n "$SUBPATH" ]; then
    ARGS="${ARGS}
            - --context-sub-path=${SUBPATH}"
  fi
  for extra in "$@"; do
    ARGS="${ARGS}
            - ${extra}"
  done

  kubectl -n "$NS" delete job "$JOB" --ignore-not-found
  kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
  namespace: ${NS}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 7200
  template:
    spec:
      serviceAccountName: kaniko-builder
      restartPolicy: Never
      containers:
        - name: kaniko
          image: gcr.io/kaniko-project/executor:v1.23.2
          args:
${ARGS}
          envFrom:
            - secretRef:
                name: git-credentials
          volumeMounts:
            - name: docker-config
              mountPath: /kaniko/.docker
          resources:
            requests:
              cpu: "1"
              memory: 4Gi
              ephemeral-storage: 10Gi
      volumes:
        - name: docker-config
          configMap:
            name: kaniko-docker-config
EOF
  echo "launched ${JOB}"
  exit 0
fi

# wait mode
t=0
while true; do
  s="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
  f="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
  if [ "${s:-0}" -ge 1 ]; then echo "${JOB} OK"; exit 0; fi
  if [ "${f:-0}" -ge 1 ]; then
    echo "::error::${JOB} failed"
    kubectl -n "$NS" logs "job/${JOB}" --tail=200 || true
    exit 1
  fi
  t=$((t + 15))
  if [ "$t" -gt 2400 ]; then echo "::error::${JOB} timeout 40m"; exit 1; fi
  sleep 15
done
