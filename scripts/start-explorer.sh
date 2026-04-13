#!/bin/bash
set -e

# Image name
IMAGE_NAME="migration-explorer"

# Build the image
echo "Building Docker image: $IMAGE_NAME..."
docker build -t $IMAGE_NAME .

# Run the container
# -v $(pwd):/app: Mount current directory to /app so you can edit locally and run inside
# -it: Interactive mode
# --network host: To easily access local databases (like localhost:3306 or tunnels)
# --rm: Remove container after exit to keep things clean
echo "Starting Explorer Shell..."
echo "You are now inside the container. Your local files are mounted at /app."
docker run -it --rm \
  -v "$(pwd):/app" \
  --network host \
  $IMAGE_NAME
