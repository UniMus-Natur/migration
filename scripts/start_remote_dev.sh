#!/bin/bash
set -e

POD_LABEL="component=migration"
LOCAL_PORT=2222
SSH_KEY="${HOME}/.ssh/id_rsa.pub"

echo "🔍 Finding migration pod..."
# Get the first running pod with the label
POD_NAME=$(kubectl get pods -l $POD_LABEL -o jsonpath="{.items[0].metadata.name}" 2>/dev/null)

if [ -z "$POD_NAME" ]; then
    echo "❌ No migration pod found! Is it deployed?"
    exit 1
fi

echo "✅ Found pod: $POD_NAME"

echo "🔑 Injecting public key..."
if [ ! -f "$SSH_KEY" ]; then
    echo "❌ Public key not found at $SSH_KEY"
    echo "Please generate one with: ssh-keygen -t rsa"
    exit 1
fi

# Ensure .ssh directory exists and append key
kubectl exec "$POD_NAME" -- mkdir -p /root/.ssh
kubectl exec "$POD_NAME" -- /bin/bash -c "echo '$(cat $SSH_KEY)' >> /root/.ssh/authorized_keys"
kubectl exec "$POD_NAME" -- chmod 600 /root/.ssh/authorized_keys

echo "✅ Key injected."

echo "🔌 Starting port-forward (Background)..."
# Kill existing forward if any
pkill -f "kubectl port-forward.*$LOCAL_PORT:22" || true
nohup kubectl port-forward "pod/$POD_NAME" $LOCAL_PORT:22 >/dev/null 2>&1 &
PF_PID=$!
echo "✅ Port-forward running (PID: $PF_PID)."

echo ""
echo "🎉 READY!"
echo "--------------------------------------------------------"
echo "You can now connect via SSH:"
echo "  ssh -p $LOCAL_PORT root@localhost"
echo ""
echo "Or configure your ~/.ssh/config for easy access:"

# Check if k8s-migration is already in config
if ! grep -q "Host k8s-migration" ~/.ssh/config 2>/dev/null; then
    echo "📋 Add this to your ~/.ssh/config (Copy-Paste once):"
    echo ""
    echo "Host k8s-migration"
    echo "    HostName localhost"
    echo "    Port $LOCAL_PORT"
    echo "    User root"
    echo "    IdentityFile ${HOME}/.ssh/id_rsa"
    echo "    StrictHostKeyChecking no"
    echo "    UserKnownHostsFile /dev/null"
    echo ""
else
    echo "✅ 'k8s-migration' host alias already detected in your config."
fi
