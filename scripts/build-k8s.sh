#!/bin/bash
set -e

# Default variables
BRANCH=$(git rev-parse --abbrev-ref HEAD)
DESTINATION="ghcr.io/unimus-natur/migration:latest"
GHCR_SECRET_NAME="ghcr-secret"

# Override defaults with arguments if provided
if [ ! -z "$1" ]; then
    BRANCH="$1"
fi

if [ ! -z "$2" ]; then
    DESTINATION="$2"
fi

echo "🚀 Starting K8s Builder..."
echo "📍 Context: $BRANCH"
echo "🎯 Destination: $DESTINATION"

# check if secret exists
if ! kubectl get secret $GHCR_SECRET_NAME >/dev/null 2>&1; then
    echo "❌ Error: Kubernetes secret '$GHCR_SECRET_NAME' not found."
    echo "Please create it with: kubectl create secret docker-registry $GHCR_SECRET_NAME --docker-server=ghcr.io --docker-username=<user> --docker-password=<token>"
    exit 1
fi

# Check if changes are pushed
if [ "$(git rev-parse --abbrev-ref HEAD)" == "$BRANCH" ]; then
    # Only check for local unpushed changes if we are building the current branch
    UNPUSHED=$(git log origin/$BRANCH..$BRANCH --oneline)
    if [ ! -z "$UNPUSHED" ]; then
        echo "⚠️  Warning: You have unpushed commits on '$BRANCH'. Build will use the REMOTE state."
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
fi

POD_NAME="migration-builder-$RANDOM"

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: $POD_NAME
spec:
  restartPolicy: Never
  volumes:
  - name: workspace
    emptyDir: {}
  - name: kaniko-secret
    projected:
      sources:
      - secret:
          name: $GHCR_SECRET_NAME
          items:
            - key: .dockerconfigjson
              path: config.json
  initContainers:
  - name: git-clone
    image: alpine/git
    args:
    - clone
    - --recursive
    - --branch
    - "$BRANCH"
    - --single-branch
    - "https://github.com/UniMus-Natur/migration.git"
    - "/workspace"
    volumeMounts:
    - name: workspace
      mountPath: /workspace
  containers:
  - name: kaniko
    image: gcr.io/kaniko-project/executor:latest
    args:
    - "--context=dir:///workspace"
    - "--destination=$DESTINATION"
    volumeMounts:
    - name: kaniko-secret
      mountPath: /kaniko/.docker
    - name: workspace
      mountPath: /workspace
    resources:
      requests:
        memory: "2Gi"
        cpu: "1000m"
      limits:
        memory: "4Gi"
        cpu: "2000m"
EOF

echo "⏳ Waiting for pod $POD_NAME to start..."
kubectl wait --for=condition=Ready pod/$POD_NAME --timeout=300s

echo "📜 Streaming logs..."
kubectl logs -f $POD_NAME

echo "🧹 Cleaning up..."
kubectl delete pod $POD_NAME
