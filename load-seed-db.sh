#!/bin/bash
set -e

# Configuration
POD_NAME="specify7-mariadb-0"
DB_USER="root"
DB_PASSWORD="root-password" # Default from bitnami values
DB_NAME="specify"
SEED_FILE="seed-databse/1.0.sql"

echo "Checking if seed file exists..."
if [ ! -f "$SEED_FILE" ]; then
    echo "Error: Seed file '$SEED_FILE' not found!"
    exit 1
fi

echo "Copying seed file to pod $POD_NAME..."
kubectl cp "$SEED_FILE" "$POD_NAME:/tmp/seed.sql"

echo "Importing database..."
kubectl exec "$POD_NAME" -- bash -c "mysql -u$DB_USER -p$DB_PASSWORD $DB_NAME < /tmp/seed.sql"

echo "Cleanup..."
kubectl exec "$POD_NAME" -- rm /tmp/seed.sql

echo "Done! Database seeded."
