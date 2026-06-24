{{/*
Expand the name of the chart.
*/}}
{{- define "specify7.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "specify7.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "specify7.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "specify7.labels" -}}
helm.sh/chart: {{ include "specify7.chart" . }}
{{ include "specify7.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "specify7.selectorLabels" -}}
app.kubernetes.io/name: {{ include "specify7.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Django ALLOWED_HOSTS: when ingress is enabled, always include ingress host(s)
so URL changes only need updating ingress.hosts.
*/}}
{{- define "specify7.allowedHosts" -}}
{{- if .Values.ingress.enabled -}}
{{- $hosts := list -}}
{{- range .Values.ingress.hosts -}}
{{- $hosts = append $hosts .host -}}
{{- end -}}
{{- range .Values.specify.allowedHosts -}}
{{- if and (ne . "*") (not (has . $hosts)) -}}
{{- $hosts = append $hosts . -}}
{{- end -}}
{{- end -}}
{{- toJson $hosts -}}
{{- else -}}
{{- toJson .Values.specify.allowedHosts -}}
{{- end -}}
{{- end -}}

{{/*
Django CSRF_TRUSTED_ORIGINS: when ingress is enabled, always include https://<ingress-host>.
*/}}
{{- define "specify7.csrfTrustedOrigins" -}}
{{- if .Values.ingress.enabled -}}
{{- $origins := list -}}
{{- range .Values.ingress.hosts -}}
{{- $origin := printf "https://%s" .host -}}
{{- $origins = append $origins $origin -}}
{{- end -}}
{{- range .Values.specify.csrfTrustedOrigins -}}
{{- if and (ne . "https://*") (ne . "http://*") (not (has . $origins)) -}}
{{- $origins = append $origins . -}}
{{- end -}}
{{- end -}}
{{- toJson $origins -}}
{{- else -}}
{{- toJson .Values.specify.csrfTrustedOrigins -}}
{{- end -}}
{{- end -}}

{{/*
Image for volume-permissions init containers (chown on shared PVCs).
*/}}
{{- define "specify7.volumePermissionsImage" -}}
{{- .Values.volumePermissions.image | default "docker.io/library/debian:bookworm-slim" }}
{{- end -}}

{{/*
Checksum annotation to roll pods when generated Specify settings change.
*/}}
{{- define "specify7.configChecksumAnnotation" -}}
checksum/config: {{ include (print $.Template.BasePath "/secret-config.yaml") . | sha256sum }}
{{- end -}}
