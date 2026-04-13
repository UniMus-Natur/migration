#!/bin/bash
set -e

POD_NAME="specify7-mariadb-0"
SECRET_NAME="${SECRET_NAME:-specify-secret}"
DB_NAME="specify"
SEED_FILE="${1:-seed-databse/1.0.sql}"

if [ ! -f "$SEED_FILE" ]; then
    echo "Error: Seed file '$SEED_FILE' not found!"
    exit 1
fi

DB_PASSWORD=$(kubectl get secret "$SECRET_NAME" \
    -o jsonpath='{.data.mariadb-root-password}' | base64 -d)

echo "Copying seed file to pod $POD_NAME..."
kubectl cp "$SEED_FILE" "$POD_NAME:/tmp/seed.sql"

echo "Importing database..."
kubectl exec "$POD_NAME" -- bash -c "mysql -uroot -p'$DB_PASSWORD' $DB_NAME < /tmp/seed.sql"

echo "Cleanup..."
kubectl exec "$POD_NAME" -- rm /tmp/seed.sql

echo "Done! Database seeded."
echo "Restart the backend pod to trigger Django migrations:"
echo "  kubectl delete pod -l component=backend"
