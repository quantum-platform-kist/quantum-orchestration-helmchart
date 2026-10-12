{{- define "qc-stateful.namespace" -}}
{{- required "global.namespace is required" .Values.global.namespace -}}
{{- end -}}

{{- define "qc-stateful.name" -}}
{{- printf "%s" .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "qc-stateful.labels" -}}
app.kubernetes.io/part-of: quantum-cloud
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "qc-stateful.selectorLabels" -}}
app.kubernetes.io/part-of: quantum-cloud
{{- end -}}

{{- define "qc-stateful.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.global.imagePullSecrets }}
  - name: {{ . | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "qc-stateful.dataPlacement" -}}
{{- with .Values.global.nodePlacement.data.nodeSelector }}
nodeSelector:
{{ toYaml . | indent 2 }}
{{- end }}
{{- with .Values.global.nodePlacement.data.affinity }}
affinity:
{{ toYaml . | indent 2 }}
{{- end }}
{{- with .Values.global.nodePlacement.data.tolerations }}
tolerations:
{{ toYaml . | indent 2 }}
{{- end }}
{{- end -}}

{{- define "qc-stateful.webPlacement" -}}
{{- with .Values.global.nodePlacement.web.nodeSelector }}
nodeSelector:
{{ toYaml . | indent 2 }}
{{- end }}
{{- with .Values.global.nodePlacement.web.affinity }}
affinity:
{{ toYaml . | indent 2 }}
{{- end }}
{{- with .Values.global.nodePlacement.web.tolerations }}
tolerations:
{{ toYaml . | indent 2 }}
{{- end }}
{{- end -}}

{{- define "qc-stateful.kafkaRetentionGuard" -}}
{{- $sum := 0 -}}
{{- range $key, $topic := .Values.kafka.topics }}
{{- $partitions := int64 (default 1 $topic.partitions) -}}
{{- $retention := int64 (default 0 $topic.retentionBytes) -}}
{{- $sum = add $sum (mul $partitions $retention) -}}
{{- end -}}
{{- $budget := int64 .Values.kafka.retentionBudgetBytes -}}
{{- if gt $sum $budget -}}
{{- fail (printf "Kafka topic retention budget exceeded: %d > %d bytes" $sum $budget) -}}
{{- end -}}
{{- end -}}

{{- define "qc-stateful.fullImage" -}}
{{- if .digest -}}
{{- printf "%s@%s" .repository .digest -}}
{{- else -}}
{{- printf "%s:%s" .repository .tag -}}
{{- end -}}
{{- end -}}
