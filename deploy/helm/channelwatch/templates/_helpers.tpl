{{- define "channelwatch.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "channelwatch.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "channelwatch.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "channelwatch.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "channelwatch.labels" -}}
helm.sh/chart: {{ include "channelwatch.chart" . }}
app.kubernetes.io/name: {{ include "channelwatch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "channelwatch.selectorLabels" -}}
app.kubernetes.io/name: {{ include "channelwatch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "channelwatch.configMapName" -}}
{{- printf "%s-config" (include "channelwatch.fullname" .) -}}
{{- end -}}

{{- define "channelwatch.secretName" -}}
{{- printf "%s-secret" (include "channelwatch.fullname" .) -}}
{{- end -}}

{{- define "channelwatch.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-config" (include "channelwatch.fullname" .) -}}
{{- end -}}
{{- end -}}
