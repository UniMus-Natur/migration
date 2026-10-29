{{/*
Expand the name of the umbrella chart.
*/}}
{{- define "unimus-specify.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "unimus-specify.fullname" -}}
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

{{- define "unimus-specify.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "unimus-specify.labels" -}}
helm.sh/chart: {{ include "unimus-specify.chart" . }}
{{ include "unimus-specify.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for UniMus workloads (Prefect, harness).

Must stay aligned with the former monolithic specify7 chart:
  app.kubernetes.io/name: specify7
Deployment.spec.selector is immutable — changing the name label forces a
recreate and breaks helm upgrade in place.
*/}}
{{- define "unimus-specify.selectorLabels" -}}
app.kubernetes.io/name: {{ default "specify7" (.Values.specify7).nameOverride }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Mirror of specify7.fullname so parent templates can address subchart services.
Assumes the specify7 subchart uses the same Release.Name.
*/}}
{{- define "unimus-specify.specify7Fullname" -}}
{{- $vals := .Values.specify7 | default dict -}}
{{- if $vals.fullnameOverride }}
{{- $vals.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default "specify7" $vals.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Existing secret for Prefect/harness: prefer umbrella secrets, fall back to subchart.
*/}}
{{- define "unimus-specify.existingSecret" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else if and .Values.specify7 .Values.specify7.secrets .Values.specify7.secrets.existingSecret -}}
{{- .Values.specify7.secrets.existingSecret -}}
{{- end -}}
{{- end }}
