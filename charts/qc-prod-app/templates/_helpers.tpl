{{- define "qc-app.namespace" -}}
{{- required "global.namespace is required" .Values.global.namespace -}}
{{- end -}}

{{- define "qc-app.labels" -}}
app.kubernetes.io/part-of: quantum-cloud
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "qc-app.image" -}}
{{- if .digest -}}
{{- printf "%s@%s" .repository .digest -}}
{{- else -}}
{{- printf "%s:%s" .repository .tag -}}
{{- end -}}
{{- end -}}

{{- define "qc-app.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.global.imagePullSecrets }}
  - name: {{ . | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "qc-app.webPlacement" -}}
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

{{- define "qc-app.springProbes" -}}
startupProbe:
  httpGet:
    path: /actuator/health
    port: http
  periodSeconds: 5
  failureThreshold: 60
readinessProbe:
  httpGet:
    path: /actuator/health
    port: http
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 3
livenessProbe:
  httpGet:
    path: /actuator/health
    port: http
  periodSeconds: 15
  timeoutSeconds: 3
  failureThreshold: 4
{{- end -}}

{{- define "qc-app.staticProbes" -}}
readinessProbe:
  httpGet:
    path: /health
    port: http
  periodSeconds: 5
  timeoutSeconds: 3
livenessProbe:
  httpGet:
    path: /health
    port: http
  periodSeconds: 15
  timeoutSeconds: 3
{{- end -}}

{{- define "qc-app.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secretName }}
      key: {{ .key }}
{{- end -}}

{{- define "qc-app.shellOrigin" -}}
{{- printf "https://%s" .Values.global.hosts.shell -}}
{{- end -}}

{{- define "qc-app.modulesOrigin" -}}
{{- printf "https://%s" .Values.global.hosts.modules -}}
{{- end -}}

