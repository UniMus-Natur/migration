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


# Calculate version
echo "🔍 Calculating version..."
NEW_VERSION=$(python3 scripts/calculate_version.py)
SHORT_HASH=$(git rev-parse --short HEAD)

echo "📏 Version: $NEW_VERSION"
echo "#️⃣  Commit:  $SHORT_HASH"

CHART_FILE="charts/specify7/Chart.yaml"
VALUES_FILE="charts/specify7/values.yaml"
STAGING_VALUES_FILE="charts/specify7/staging.values.yaml"

if [ "$BRANCH" == "main" ]; then
    echo "🌿 Branch is main. Handling release..."
    
    # Check if we need to tag
    if git rev-parse "$NEW_VERSION" >/dev/null 2>&1; then
        echo "✅ Tag $NEW_VERSION already exists."
    else
        echo "🆕 New version detected: $NEW_VERSION"
        
        # Update files
        echo "📝 Updating Chart.yaml and values.yaml..."
        python3 scripts/calculate_version.py --update "$NEW_VERSION" "$CHART_FILE" "$VALUES_FILE"
        
        # Commit and Tag
        echo "💾 Committing and Tagging..."
        git add "$CHART_FILE" "$VALUES_FILE"
        git commit -m "chore: release $NEW_VERSION"
        git tag "$NEW_VERSION"
        
        echo "🚀 Pushing to origin..."
        git push origin "$BRANCH" --tags
    fi
    
    DESTINATION="${DESTINATION%%:*}:$NEW_VERSION"
    BUILD_CONTEXT="git://github.com/UniMus-Natur/migration.git#refs/tags/$NEW_VERSION"
else
    echo "🌿 Branch is $BRANCH (not main)."
    DESTINATION="${DESTINATION%%:*}:$SHORT_HASH"
    
    # Update staging values if it exists
    if [ -f "$STAGING_VALUES_FILE" ]; then
        echo "📝 Updating staging.values.yaml..."
        # note: passing None for Chart.yaml to skip it? 
        # My python script expects 3 args strictly for update mode + optional 4th.
        # Let's adjust usage or just pass the files.
        # Actually I can update values.yaml locally for dev too?
        # User said: "helm chart should always follow the latest".
        # Let's update `staging.values.yaml` with the short hash.
        # We need to hack the python script usage or just use sed here for simplicity? 
        # Or better, reuse the python tool but be careful.
        # The python tool updates Chart AND Values. 
        # I only want to update staging values for non-main? 
        # Or maybe I update Chart.yaml appVersion too?
        # Let's assume updating check files is okay locally.
        
        # Using a temporary python snippet might be easier given the strict args I wrote? 
        # Or just use sed for this simple case
        case "$(uname -s)" in
            Darwin) sed -i '' -E "s/(\s+tag:\s*)\"[^\"]+\"/\1\"$SHORT_HASH\"/" "$STAGING_VALUES_FILE" ;;
            *)      sed -i -E "s/(\s+tag:\s*)\"[^\"]+\"/\1\"$SHORT_HASH\"/" "$STAGING_VALUES_FILE" ;;
        esac
    fi
    # Use branch for build context
    BUILD_CONTEXT="git://github.com/UniMus-Natur/migration.git#refs/heads/$BRANCH"
fi

echo "🎯 Build Destination: $DESTINATION"

echo "🚀 Starting K8s Builder..."
echo "📍 Context: $BRANCH"
# The original "🎯 Destination" echo is now redundant with "🎯 Build Destination" above.
# echo "🎯 Destination: $DESTINATION" 

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
    - "--destination=$DESTINATION"
    - "--git=recurse-submodules=true"

    volumeMounts:
    - name: kaniko-secret
      mountPath: /kaniko/.docker
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
