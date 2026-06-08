{{/*
Expand the name of the chart.
*/}}
{{- define "taas.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "taas.fullname" -}}
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
Create chart label.
*/}}
{{- define "taas.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "taas.labels" -}}
helm.sh/chart: {{ include "taas.chart" . }}
{{ include "taas.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "taas.selectorLabels" -}}
app.kubernetes.io/name: {{ include "taas.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service-specific labels (pass service name as .service).
Usage: {{ include "taas.serviceLabels" (dict "service" "gateway" "root" .) }}
*/}}
{{- define "taas.serviceLabels" -}}
helm.sh/chart: {{ include "taas.chart" .root }}
app.kubernetes.io/name: {{ printf "%s-%s" (include "taas.name" .root) .service }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .service }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/part-of: taas
{{- end }}

{{/*
Image reference helper.
Usage: {{ include "taas.image" (dict "repo" .Values.gateway.image.repository "root" .) }}
*/}}
{{- define "taas.image" -}}
{{- if .root.Values.global.imageRegistry -}}
{{- printf "%s/%s:%s" .root.Values.global.imageRegistry .repo .root.Values.image.tag -}}
{{- else -}}
{{- printf "%s:%s" .repo .root.Values.image.tag -}}
{{- end -}}
{{- end }}

{{/*
Pin locally built images to the node where scripts/deploy-local-k8s.sh imported
them. This is only used when localImages.nodeName is set.
*/}}
{{- define "taas.localImageNodeSelector" -}}
{{- if .Values.localImages.nodeName }}
nodeSelector:
  kubernetes.io/hostname: {{ .Values.localImages.nodeName | quote }}
{{- end }}
{{- end }}

{{/*
PostgreSQL service hostname (Bitnami subchart default).
*/}}
{{- define "taas.postgresqlHost" -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- end }}

{{/*
Redis service hostname (Bitnami subchart default master service).
*/}}
{{- define "taas.redisHost" -}}
{{- printf "%s-redis-master" .Release.Name -}}
{{- end }}

{{/*
Embedded/external NATS URL.
*/}}
{{- define "taas.natsUrl" -}}
{{- if .Values.nats.embedded.enabled -}}
{{- printf "nats://%s-nats:4222" (include "taas.fullname" .) -}}
{{- else -}}
{{- .Values.nats.url -}}
{{- end -}}
{{- end }}

{{/*
Web nginx config (SPA + /api proxy to gateway).
*/}}
{{- define "taas.web.nginx.conf" -}}
server {
    listen {{ .Values.web.containerPort }};
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://{{ include "taas.fullname" . }}-gateway:{{ .Values.gateway.service.port }}/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
{{- end }}
