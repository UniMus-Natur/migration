#!/usr/bin/env bash
set -euo pipefail

# Build the specify7 submodule (production target) with in-cluster Kaniko
# and push it to GHCR, next to the Prefect dev-worker image
# (ghcr.io/unimus-natur/migration).
#
# Commit in the specify7 submodule first. This script pushes that commit to
# the migration repo (refs/tags/specify7-<sha>) and Kaniko clones it from
# there. The tag's tree is the Specify 7 source.
#
# Usage:
#   ./scripts/build-specify7-k8s.sh
#   ./scripts/build-specify7-k8s.sh ghcr.io/unimus-natur/specify7-service
#
# On success, charts/unimus-specify/staging.values.yaml is pointed at the
# new image. Deploy with:
#   helm --kube-context nird upgrade specify7 ./charts/unimus-specify \
#     -n unimus-ns12130k \
#     -f charts/unimus-specify/staging.values.yaml
#
# Webpack + pip need memory. Limit must stay within 2x the request.
#   KANIKO_REQUEST_MEMORY=4Gi KANIKO_LIMIT_MEMORY=8Gi ./scripts/build-specify7-k8s.sh

DESTINATION_REPO="${1:-ghcr.io/unimus-natur/specify7-service}"
DESTINATION_REPO="${DESTINATION_REPO%%:*}"
GHCR_SECRET_NAME="${GHCR_SECRET_NAME:-ghcr-secret}"
KUBE_CONTEXT="${KUBE_CONTEXT:-nird}"
KUBE_NAMESPACE="${KUBE_NAMESPACE:-unimus-ns12130k}"
STAGING_VALUES_FILE="${STAGING_VALUES_FILE:-charts/unimus-specify/staging.values.yaml}"
KANIKO_REQUEST_CPU="${KANIKO_REQUEST_CPU:-1000m}"
KANIKO_REQUEST_MEMORY="${KANIKO_REQUEST_MEMORY:-2Gi}"
KANIKO_LIMIT_CPU="${KANIKO_LIMIT_CPU:-2000m}"
KANIKO_LIMIT_MEMORY="${KANIKO_LIMIT_MEMORY:-4Gi}"

KUBECTL=(kubectl --context="$KUBE_CONTEXT" --namespace="$KUBE_NAMESPACE")

if ! git -C specify7 rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "❌ specify7 submodule is missing. Run: git submodule update --init specify7"
    exit 1
fi

if ! git -C specify7 diff --quiet || ! git -C specify7 diff --cached --quiet; then
    echo "❌ specify7 has uncommitted changes."
    echo "Commit them in the submodule, then rerun. The script pushes that commit to the migration repo."
    exit 1
fi

FULL_SHA="$(git -C specify7 rev-parse HEAD)"
SHORT_SHA="$(git -C specify7 rev-parse --short=12 HEAD)"
if BUILD_VERSION="$(git -C specify7 describe --tags --exact-match HEAD 2>/dev/null)"; then
    :
else
    BUILD_VERSION="$SHORT_SHA"
fi

MIGRATION_URL="$(git remote get-url origin)"
SOURCE_TAG="specify7-${FULL_SHA}"
echo "📤 Pushing specify7 ${FULL_SHA} to ${MIGRATION_URL} (refs/tags/${SOURCE_TAG})..."
git -C specify7 push "$MIGRATION_URL" "HEAD:refs/tags/${SOURCE_TAG}"

case "$MIGRATION_URL" in
    git@*)
        REMOTE_PATH="${MIGRATION_URL#git@}"
        REMOTE_PATH="${REMOTE_PATH/://}"
        ;;
    http://*|https://*|git://*)
        REMOTE_PATH="${MIGRATION_URL#http://}"
        REMOTE_PATH="${REMOTE_PATH#https://}"
        REMOTE_PATH="${REMOTE_PATH#git://}"
        ;;
    *)
        echo "❌ Unrecognized migration remote URL: $MIGRATION_URL"
        exit 1
        ;;
esac
REMOTE_PATH="${REMOTE_PATH%.git}"
BUILD_CONTEXT="git://${REMOTE_PATH}.git#refs/tags/${SOURCE_TAG}"
DESTINATION="${DESTINATION_REPO}:${SHORT_SHA}"

echo "🎯 Build destination: $DESTINATION"
echo "📍 specify7: $FULL_SHA ($BUILD_VERSION)"
echo "📄 Context: $BUILD_CONTEXT (target run)"
echo "🧠 Resources: requests(cpu=$KANIKO_REQUEST_CPU,mem=$KANIKO_REQUEST_MEMORY) limits(cpu=$KANIKO_LIMIT_CPU,mem=$KANIKO_LIMIT_MEMORY)"

if ! "${KUBECTL[@]}" get secret "$GHCR_SECRET_NAME" >/dev/null 2>&1; then
    echo "❌ Kubernetes secret '$GHCR_SECRET_NAME' not found in $KUBE_CONTEXT/$KUBE_NAMESPACE."
    echo "The Prefect dev-worker image already uses this secret to pull ghcr.io/unimus-natur/migration."
    exit 1
fi

POD_NAME="specify7-builder-$RANDOM"

cat <<EOF | "${KUBECTL[@]}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: $POD_NAME
  labels:
    app: specify7-builder
spec:
  restartPolicy: Never
  volumes:
  - name: kaniko-secret
    projected:
      sources:
      - secret:
          name: $GHCR_SECRET_NAME
          items:
          - key: .dockerconfigjson
            path: config.json
  containers:
  - name: kaniko
    image: gcr.io/kaniko-project/executor:latest
    args:
    - "--context=$BUILD_CONTEXT"
    - "--dockerfile=Dockerfile"
    - "--target=run"
    - "--destination=$DESTINATION"
    - "--build-arg=BUILD_VERSION=$BUILD_VERSION"
    - "--build-arg=GIT_SHA=$FULL_SHA"
    volumeMounts:
    - name: kaniko-secret
      mountPath: /kaniko/.docker
    resources:
      requests:
        memory: "$KANIKO_REQUEST_MEMORY"
        cpu: "$KANIKO_REQUEST_CPU"
      limits:
        memory: "$KANIKO_LIMIT_MEMORY"
        cpu: "$KANIKO_LIMIT_CPU"
EOF

cleanup() {
    echo "🧹 Cleaning up..."
    "${KUBECTL[@]}" delete pod "$POD_NAME" --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "⏳ Waiting for pod $POD_NAME to start..."
"${KUBECTL[@]}" wait --for=condition=Ready "pod/$POD_NAME" --timeout=300s

echo "📜 Streaming logs..."
"${KUBECTL[@]}" logs -f "$POD_NAME"

echo "🔎 Checking build result..."
KANIKO_EXIT_CODE="$("${KUBECTL[@]}" get pod "$POD_NAME" -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}' 2>/dev/null || true)"
if [ -z "$KANIKO_EXIT_CODE" ]; then
    KANIKO_EXIT_CODE="$("${KUBECTL[@]}" get pod "$POD_NAME" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.exitCode}' 2>/dev/null || true)"
fi

if [ -z "$KANIKO_EXIT_CODE" ]; then
    echo "❌ Could not determine Kaniko exit code. Treating build as failed."
    exit 1
fi

if [ "$KANIKO_EXIT_CODE" != "0" ]; then
    echo "❌ Build failed (Kaniko exit code: $KANIKO_EXIT_CODE)"
    exit "$KANIKO_EXIT_CODE"
fi

if [ -f "$STAGING_VALUES_FILE" ]; then
    echo "📝 Updating $STAGING_VALUES_FILE..."
    SPECIFY7_IMAGE_REPO="$DESTINATION_REPO" \
    SPECIFY7_IMAGE_TAG="$SHORT_SHA" \
    STAGING_VALUES_FILE="$STAGING_VALUES_FILE" \
    python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["STAGING_VALUES_FILE"])
repo = os.environ["SPECIFY7_IMAGE_REPO"]
tag = os.environ["SPECIFY7_IMAGE_TAG"]
lines = path.read_text().splitlines()
out = []
in_specify7 = False
in_image = False
found = {"repository": False, "tag": False, "pullPolicy": False}

for line in lines:
    stripped = line.lstrip()
    indent = len(line) - len(stripped) if stripped else None
    if indent == 0 and stripped.startswith("specify7:"):
        in_specify7 = True
        in_image = False
        out.append(line)
        continue
    if in_specify7 and indent == 0 and stripped and not stripped.startswith("#"):
        in_specify7 = False
        in_image = False
    if in_specify7 and not in_image and indent == 2 and stripped.startswith("image:"):
        in_image = True
        out.append(line)
        continue
    if in_image and indent is not None and indent <= 2 and stripped and not stripped.startswith("#"):
        in_image = False
    if in_image and indent == 4 and ":" in stripped:
        key = stripped.split(":", 1)[0].strip()
        if key == "repository":
            out.append(f"    repository: {repo}")
            found[key] = True
            continue
        if key == "tag":
            out.append(f"    tag: {tag}")
            found[key] = True
            continue
        if key == "pullPolicy":
            out.append("    pullPolicy: Always")
            found[key] = True
            continue
    out.append(line)

missing = [key for key, ok in found.items() if not ok]
if missing:
    raise SystemExit(f"specify7.image is missing keys: {', '.join(missing)}")

path.write_text("\n".join(out) + "\n")
PY
else
    echo "⚠️  $STAGING_VALUES_FILE not found. Set specify7.image yourself:"
    echo "    repository: $DESTINATION_REPO"
    echo "    tag: $SHORT_SHA"
    echo "    pullPolicy: Always"
fi

echo "✅ Pushed $DESTINATION"
echo "Deploy it with:"
echo "  helm --kube-context nird upgrade specify7 ./charts/unimus-specify \\"
echo "    -n unimus-ns12130k \\"
echo "    -f charts/unimus-specify/staging.values.yaml"
