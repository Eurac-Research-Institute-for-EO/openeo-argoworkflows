# Redeployment Runbook — OpenEO on eosao42.eurac.edu

This runbook covers full redeployment of the OpenEO backend on a fresh VM.
Follow steps in order.

---

## Prerequisites

- Ubuntu VM (16 cores, 62 GB RAM, 450+ GB disk recommended)
- `snap` package manager available (standard on Ubuntu)
- GitHub access to `Eurac-Research-Institute-for-EO` org (for pulling images + cloning repos)
- Keycloak access to `edp-portal.eurac.edu` (to get OIDC client credentials)

---

## DNS Setup (openeo.eurac.edu)

The current DNS setup is **not a direct DNS record to the VM**. It goes through an Apache reverse proxy:

```
openeo.eurac.edu → Andrea's server (Apache reverse proxy) → <VM_IP>:80
```

### If the VM IP changes

1. Ask **Andrea** to update the Apache reverse proxy target to the new VM IP.
2. The DNS record (`openeo.eurac.edu`) itself does not need to change — it always points to Andrea's server.

### If starting completely from scratch (new hostname)

1. Submit an **ICT ticket** to Eurac IT to create a DNS record pointing the desired hostname to the new VM IP.
2. Request a **TLS certificate** for the hostname in the same ticket.
3. Update the Keycloak client `openeo-apisix` redirect URIs to use the new hostname:
   - edp-portal.eurac.edu → Clients → `openeo-apisix` → Valid Redirect URIs
4. Update the helm deployment: `--set global.env.apiDns=<new-hostname>`

---

## Step 1 — Install microk8s

```bash
sudo snap install microk8s --classic --channel=1.33/stable
sudo usermod -aG microk8s $USER
newgrp microk8s
microk8s status --wait-ready
```

Enable required addons:

```bash
microk8s enable dns
microk8s enable hostpath-storage
microk8s enable ingress
microk8s enable cert-manager
microk8s enable dashboard
microk8s enable metrics-server
microk8s enable helm3
microk8s enable registry
```

Alias kubectl and helm for convenience:

```bash
echo "alias kubectl='microk8s kubectl'" >> ~/.bashrc
echo "alias helm='microk8s helm3'" >> ~/.bashrc
source ~/.bashrc
```

---

## Step 2 — Install k9s (optional but useful)

```bash
mkdir -p ~/bin
curl -sL https://github.com/derailed/k9s/releases/latest/download/k9s_Linux_amd64.tar.gz | tar xz -C ~/bin k9s
```

Usage: `~/bin/k9s -n openeo -c pods`

---

## Step 3 — Clone repos

```bash
# Helm chart
mkdir -p ~/charts/eodc
git clone https://github.com/Eurac-Research-Institute-for-EO/charts.git ~/charts/eodc/openeo-argo

# Source repo (for deploy values and docs)
git clone https://github.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows.git /tmp/openeo-argoworkflows-src
cd /tmp/openeo-argoworkflows-src && git checkout eurac-main
```

---

## Step 4 — Set up GHCR image pull secret

The Docker images are in a private GitHub Container Registry org. You need a GitHub personal access token (PAT) with `read:packages` scope.

```bash
microk8s kubectl create namespace openeo

microk8s kubectl create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=<YOUR_GITHUB_USERNAME> \
  --docker-password=<YOUR_GITHUB_PAT> \
  --docker-email=<YOUR_EMAIL> \
  -n openeo
```

Then patch the default service account to use it:

```bash
microk8s kubectl patch serviceaccount default -n openeo \
  -p '{"imagePullSecrets": [{"name": "ghcr-pull-secret"}]}'
```

---

## Step 5 — Create secrets

### PostgreSQL password

```bash
microk8s kubectl create secret generic openeo-postgresql \
  --from-literal=postgres-password=openeo123 \
  -n openeo
```

> Note: `openeo123` is the current password in use. Change it if needed — make sure it matches what you pass to helm (`postgresql.auth.password`).

### Signed URL key

```bash
microk8s kubectl create secret generic openeo-sign-key \
  --from-literal=sign-secret=<RANDOM_SECRET_STRING> \
  -n openeo
```

Use any random string, e.g.: `openssl rand -hex 32`

### TLS certificates

Create TLS secrets from the certificate files provided by ICT:

```bash
microk8s kubectl create secret tls openeo-tls \
  --cert=/path/to/tls.crt \
  --key=/path/to/tls.key \
  -n openeo

microk8s kubectl create secret tls openeo-eurac-tls \
  --cert=/path/to/tls.crt \
  --key=/path/to/tls.key \
  -n openeo
```

---

## Step 6 — Deploy OpenEO via Helm

```bash
microk8s helm3 upgrade --install openeo ~/charts/eodc/openeo-argo -n openeo \
  --set image.repository="ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-api" \
  --set image.tag="latest" \
  --set image.pullPolicy="Always" \
  --set global.env.executorImage="ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-executor:latest" \
  --set global.env.oidcUrl="https://edp-portal.eurac.edu/auth/realms/edp" \
  --set global.env.oidcOrganisation="eurac" \
  --set global.env.stacCatalogueUrl="https://stac.eurac.edu" \
  --set global.env.apiDns="openeo.eurac.edu" \
  --set global.env.apiTLS="true" \
  --set global.env.daskWorkerCores="1" \
  --set global.env.daskWorkerMemory="4" \
  --set global.env.daskWorkerLimit="2" \
  --set global.env.daskClusterTimeout="3600" \
  --set postgresql.auth.password="openeo123" \
  --set postgresql.image.repository="bitnamilegacy/postgresql" \
  --set redis.image.repository="bitnamilegacy/redis" \
  --set redis.replica.replicaCount=1 \
  --set persistence.accessModes[0]="ReadWriteOnce" \
  --set persistence.capacity="8Gi" \
  --set dask-gateway.traefik.service.type=ClusterIP \
  --set argoworkflows.enabled=true \
  --set argoworkflows.singleNamespace=true \
  --set cleanup.retentionDays=30
```

Wait for rollout:

```bash
microk8s kubectl rollout status deployment/openeo-openeo-argo -n openeo --timeout=180s
```

---

## Step 7 — Apply Nginx Ingress

The ingress exposes `/openeo` and `/.well-known` paths for the configured hostnames.
Replace `<YOUR_HOSTNAME>` with your actual hostname (e.g. `openeo.eurac.edu`):

```bash
cat <<EOF | microk8s kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: openeo-ingress
  namespace: openeo
  annotations:
    nginx.ingress.kubernetes.io/enable-cors: "true"
    nginx.ingress.kubernetes.io/cors-allow-origin: "*"
    nginx.ingress.kubernetes.io/cors-allow-methods: "GET, POST, PUT, DELETE, OPTIONS"
    nginx.ingress.kubernetes.io/cors-allow-headers: "DNT,X-CustomHeader,Keep-Alive,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Authorization"
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
spec:
  ingressClassName: nginx
  rules:
  - host: <YOUR_HOSTNAME>
    http:
      paths:
      - path: /openeo
        pathType: Prefix
        backend:
          service:
            name: openeo-openeo-argo
            port:
              number: 8000
      - path: /.well-known
        pathType: Prefix
        backend:
          service:
            name: openeo-openeo-argo
            port:
              number: 8000
  tls:
  - hosts:
    - <YOUR_HOSTNAME>
    secretName: openeo-tls
EOF
```

---

## Step 8 — Patch RBAC for Calrissian (CWL execution)

The Helm chart creates `openeo-argo-pods-role` with only `get/list/watch`.
Calrissian needs `create/delete/patch` to spawn CWL step pods:

```bash
microk8s kubectl patch role openeo-argo-pods-role -n openeo --type=json -p='[
  {"op": "replace", "path": "/rules/0/verbs", "value": ["get","list","watch","create","delete","patch"]}
]'
```

---

## Step 9 — Create Argo SA token secret

The API needs a long-lived token to submit Argo Workflows:

```bash
cat <<EOF | microk8s kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: openeo-argo-access-sa.service-account-token
  namespace: openeo
  annotations:
    kubernetes.io/service-account.name: openeo-argo-access-sa
type: kubernetes.io/service-account-token
EOF
```

Verify token is populated (may take a few seconds):

```bash
microk8s kubectl get secret openeo-argo-access-sa.service-account-token -n openeo \
  -o jsonpath='{.data.token}' | base64 -d | head -c 20
```

---

## Step 10 — Deploy APISIX

APISIX is used as the API gateway. It currently has one route that rewrites response bodies for the upstream EOEPCA deployment. For the in-house deployment it is installed but the route config below is the actual running config.

```bash
microk8s helm3 repo add apisix https://charts.apiseven.com
microk8s helm3 repo update

microk8s helm3 install apisix apisix/apisix \
  --namespace ingress-apisix \
  --create-namespace \
  --set gateway.type=NodePort \
  --set ingress-controller.enabled=true \
  --version 2.12.6
```

Wait for APISIX to be ready:

```bash
microk8s kubectl rollout status deployment/apisix -n ingress-apisix --timeout=120s
```

### Apply the OpenEO route via Admin API

The APISIX admin API runs on port 9180 (localhost only). The default admin key is `edd1c9f034335f136f87ad84b625c8f1` — change this in production.

```bash
curl -X PUT http://127.0.0.1:9180/apisix/admin/routes/openeo-upstream \
  -H "X-API-KEY: edd1c9f034335f136f87ad84b625c8f1" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "openeo-upstream",
    "name": "openeo-eurac-upstream",
    "desc": "OpenEO route for upstream with URL rewriting",
    "uri": "/*",
    "host": "openeo-eurac.develop.eoepca.org",
    "vars": [["uri", "~~", "^/(\\.well-known.*|openeo/1\\.1\\.0/.*)"]],
    "upstream": {
      "type": "roundrobin",
      "scheme": "http",
      "nodes": {
        "openeo-openeo-argo.openeo.svc.cluster.local:8000": 1
      }
    },
    "plugins": {
      "cors": {
        "allow_origins": "*",
        "allow_methods": "*",
        "allow_headers": "*",
        "max_age": 5
      },
      "serverless-post-function": {
        "phase": "header_filter",
        "functions": [
          "return function(conf, ctx)\n  local ngx = ngx\n  ngx.ctx.api_ctx = ngx.ctx.api_ctx or {}\n  ngx.ctx.api_ctx.need_body_rewrite = true\nend"
        ]
      },
      "serverless-pre-function": {
        "phase": "body_filter",
        "functions": [
          "return function(conf, ctx)\n  local ngx = ngx\n  local ctx_api = ngx.ctx.api_ctx\n  if ctx_api and ctx_api.need_body_rewrite and ngx.arg[1] then\n    local body = ngx.arg[1]\n    body = string.gsub(body, '\''https://openeo%-dev%.eurac%.edu/openeo/1%.1%.0/'\'', '\''https://openeo-eurac.develop.eoepca.org/openeo/1.1.0/'\'')\n    body = string.gsub(body, '\''\\"id\\":\\"egi\\"'\'', '\''\\"id\\":\\"eurac-keycloak\\"'\'')\n    body = string.gsub(body, '\''\\"issuer\\":\\"https://aai%.egi%.eu/auth/realms/egi\\"'\'', '\''\\"issuer\\":\\"https://edp-portal.eurac.edu/auth/realms/edp\\"'\'')\n    body = string.gsub(body, '\''\\"title\\":\\"EGI Check%-in\\"'\'', '\''\\"title\\":\\"EURAC Keycloak\\"'\'')\n    ngx.arg[1] = body\n  end\nend"
        ]
      }
    }
  }'
```

APISIX gateway is available at NodePort `32120` (HTTP).

---

## Step 11 — Deploy Monitoring Stack (optional)

### Add Grafana Helm repo

```bash
microk8s helm3 repo add grafana https://grafana.github.io/helm-charts
microk8s helm3 repo update
microk8s kubectl create namespace monitoring
```

### Loki

```bash
microk8s helm3 install loki grafana/loki \
  --namespace monitoring \
  --version 6.55.0 \
  --set loki.commonConfig.replication_factor=1 \
  --set loki.storage.type=filesystem \
  --set singleBinary.replicas=1
```

### Promtail

```bash
microk8s helm3 install promtail grafana/promtail \
  --namespace monitoring \
  --version 6.17.1 \
  --set "config.clients[0].url=http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1/push"
```

### Grafana

```bash
microk8s helm3 install grafana grafana/grafana \
  --namespace monitoring \
  --version 10.5.15 \
  --set adminPassword="openeo-admin" \
  --set service.type=NodePort \
  --set service.nodePort=31000 \
  --set sidecar.rules.enabled=false
```

After install, add Loki as a data source in Grafana UI (http://\<VM_IP\>:31000):
- Type: Loki
- URL: `http://loki-gateway.monitoring.svc.cluster.local/`

---

## Step 12 — Verify deployment

```bash
# Capabilities
curl -skL https://openeo.eurac.edu/ | python3 -m json.tool | grep -E '"api_version"|"title"'

# File formats
curl -skL https://openeo.eurac.edu/openeo/1.1.0/file_formats | python3 -m json.tool

# Collections count
curl -skL https://openeo.eurac.edu/openeo/1.1.0/collections | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('Collections:', len(d['collections']))"

# Check /file_formats appears in capabilities endpoints
curl -skL https://openeo.eurac.edu/openeo/1.1.0/ | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print([e['path'] for e in d['endpoints']])"
```

---

## Key URLs after deployment

| Service | URL |
|---------|-----|
| OpenEO API | https://openeo.eurac.edu |
| Argo Workflows UI | http://\<VM_IP\>:31635 |
| Kubernetes Dashboard | http://\<VM_IP\>:30443 |
| Grafana | http://\<VM_IP\>:31000 (admin / openeo-admin) |
| APISIX gateway | http://\<VM_IP\>:32120 |

---

## Secrets to collect before VM is lost

If you have advance warning before the VM is killed, run these to save critical data:

```bash
# PostgreSQL password
microk8s kubectl get secret openeo-postgresql -n openeo \
  -o jsonpath='{.data.postgres-password}' | base64 -d

# Sign key
microk8s kubectl get secret openeo-sign-key -n openeo \
  -o jsonpath='{.data.sign-secret}' | base64 -d

# Argo SA token
microk8s kubectl get secret openeo-argo-access-sa.service-account-token -n openeo \
  -o jsonpath='{.data.token}' | base64 -d

# PostgreSQL dump
PG_PASS=$(microk8s kubectl get secret openeo-postgresql -n openeo \
  -o jsonpath='{.data.postgres-password}' | base64 -d)
microk8s kubectl exec -n openeo openeo-postgresql-0 -- bash -c \
  "PGPASSWORD='$PG_PASS' pg_dump -U postgres postgres" > openeo-db-backup-$(date +%Y%m%d).sql
```

---

## What lives outside this VM (safe — no action needed)

| What | Where |
|------|-------|
| Source code | GitHub `Eurac-Research-Institute-for-EO/openeo-argoworkflows` |
| Helm chart | GitHub `Eurac-Research-Institute-for-EO/charts` |
| Docker images | GHCR `ghcr.io/eurac-research-institute-for-eo/` |
| STAC catalog | stac.eurac.edu (Bart's server) |
| Keycloak | edp-portal.eurac.edu |
| EOEPCA upstream | Separate EOEPCA cluster |
