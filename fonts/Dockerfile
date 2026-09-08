# Jasper font-extension jar for specifyconsortium/report-runner.
# Copied into WEB-INF/lib by the Helm chart (ConfigMaps cannot hold this file).
# Build from the migration repo root:
#   podman build --platform linux/amd64 -t ghcr.io/unimus-natur/report-fonts:latest -f fonts/Dockerfile .
FROM docker.io/library/busybox:1.37
COPY fonts/report-fonts.jar /report-fonts.jar
