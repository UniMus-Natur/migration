#!/bin/bash
set -e

# Build only the migration-harness image via in-cluster Kaniko.
#
# Usage:
#   ./scripts/build-migration-harness-k8s.sh
#   ./scripts/build-migration-harness-k8s.sh <branch> [destination]
#
# Examples:
#   ./scripts/build-migration-harness-k8s.sh
#   ./scripts/build-migration-harness-k8s.sh dev
#   ./scripts/build-migration-harness-k8s.sh dev ghcr.io/unimus-natur/migration-harness

BRANCH=$(git rev-parse --abbrev-ref HEAD)
DESTINATION_REPO="ghcr.io/unimus-natur/migration-harness"
GHCR_SECRET_NAME="ghcr-secret"
DOCKERFILE_PATH="services/migration-harness/Dockerfile"
KANIKO_REQUEST_CPU="${KANIKO_REQUEST_CPU:-250m}"
KANIKO_REQUEST_MEMORY="${KANIKO_REQUEST_MEMORY:-512Mi}"
KANIKO_LIMIT_CPU="${KANIKO_LIMIT_CPU:-500m}"
KANIKO_LIMIT_MEMORY="${KANIKO_LIMIT_MEMORY:-1Gi}"

if [ ! -z "$1" ]; then
    BRANCH="$1"
fi

if [ ! -z "$2" ]; then
    DESTINATION_REPO="${2%%:*}"
fi

SHORT_HASH=$(git rev-parse --short HEAD)

if [ "$BRANCH" == "main" ]; then
    TAG="$SHORT_HASH"
    BUILD_CONTEXT="git://github.com/UniMus-Natur/migration.git#refs/heads/$BRANCH"
else
    TAG="$SHORT_HASH"
    BUILD_CONTEXT="git://github.com/UniMus-Natur/migration.git#refs/heads/$BRANCH"
fi

DESTINATION="$DESTINATION_REPO:$TAG"

echo "🎯 Build Destination: $DESTINATION"
echo "📍 Branch: $BRANCH"
echo "📄 Dockerfile: $DOCKERFILE_PATH"
echo "🧠 Resources: requests(cpu=$KANIKO_REQUEST_CPU,mem=$KANIKO_REQUEST_MEMORY) limits(cpu=$KANIKO_LIMIT_CPU,mem=$KANIKO_LIMIT_MEMORY)"

if ! kubectl get secret $GHCR_SECRET_NAME >/dev/null 2>&1; then
    echo "❌ Error: Kubernetes secret '$GHCR_SECRET_NAME' not found."
    echo "Please create it with: kubectl create secret docker-registry $GHCR_SECRET_NAME --docker-server=ghcr.io --docker-username=<user> --docker-password=<token>"
    exit 1
fi

if [ "$(git rev-parse --abbrev-ref HEAD)" == "$BRANCH" ]; then
    UNPUSHED=$(git log origin/$BRANCH..$BRANCH --oneline || true)
    if [ ! -z "$UNPUSHED" ]; then
        echo "⚠️  Warning: You have unpushed commits on '$BRANCH'. Build will use the REMOTE state."
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
fi

POD_NAME="migration-harness-builder-$RANDOM"

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: $POD_NAME
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
    - "--dockerfile=$DOCKERFILE_PATH"
    - "--destination=$DESTINATION"
    - "--git=recurse-submodules=true"
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

echo "⏳ Waiting for pod $POD_NAME to start..."
kubectl wait --for=condition=Ready pod/$POD_NAME --timeout=300s

echo "📜 Streaming logs..."
kubectl logs -f $POD_NAME

echo "🧹 Cleaning up..."
kubectl delete pod $POD_NAME

echo "✅ Done: $DESTINATION"
