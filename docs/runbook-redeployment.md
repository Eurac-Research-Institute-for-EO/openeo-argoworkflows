# Redeployment Runbook — OpenEO on eosao42.eurac.edu

This runbook covers full redeployment of the OpenEO backend on a fresh VM.
Follow steps in order.

---

## Prerequisites

- Ubuntu VM (16 cores, 62 GB RAM, 450+ GB disk)
- Public hostname pointing to the VM (openeo.eurac.edu → VM IP)
- GitHub access to `Eurac-Research-Institute-for-EO` org
- Keycloak client credentials for `openeo-apisix` (from edp-portal.eurac.edu)
- Keycloak client credentials for `openeo-platform-default-client`

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
3. Update the Keycloak client `openeo-apisix` redirect URIs to use the new hostname (edp-portal.eurac.edu → Clients → openeo-apisix → Valid Redirect URIs).
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

# Source repo
git clone https://github.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows.git /tmp/openeo-argoworkflows-src
cd /tmp/openeo-argoworkflows-src && git checkout eurac-main
```

---

## Step 4 — Create namespace and secrets

```bash
microk8s kubectl create namespace openeo
```

### PostgreSQL password secret

```bash
microk8s kubectl create secret generic openeo-postgresql \
  --from-literal=postgres-password=<POSTGRES_PASSWORD> \
  -n openeo
```

### Signed URL key secret

```bash
microk8s kubectl create secret generic openeo-sign-key \
  --from-literal=sign-secret=<SIGN_SECRET> \
  -n openeo
```

### TLS certificates

If using cert-manager with Let's Encrypt, apply a Certificate resource.
Otherwise, create TLS secrets manually:

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

## Step 5 — Deploy OpenEO via Helm

```bash
helm upgrade --install openeo ~/charts/eodc/openeo-argo -n openeo \
  --reuse-values \
  --set image.repository="ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-api" \
  --set image.tag="latest" \
  --set image.pullPolicy="Always" \
  --set global.env.executorImage="ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-executor:latest" \
  --set global.env.oidcUrl="https://edp-portal.eurac.edu/auth/realms/edp" \
  --set global.env.oidcOrganisation="eurac" \
  --set global.env.stacCatalogueUrl="https://stac.eurac.edu" \
  --set global.env.apiDns="openeo.eurac.edu" \
  --set global.env.apiTLS="true" \
  --set persistence.accessModes[0]="ReadWriteOnce"
```

Wait for rollout:

```bash
microk8s kubectl rollout status deployment/openeo-openeo-argo -n openeo --timeout=120s
```

---

## Step 6 — Patch RBAC for Calrissian (CWL execution)

The Helm chart creates `openeo-argo-pods-role` with only `get/list/watch`.
Calrissian needs `create/delete/patch` to spawn CWL step pods:

```bash
microk8s kubectl patch role openeo-argo-pods-role -n openeo --type=json -p='[
  {"op": "replace", "path": "/rules/0/verbs", "value": ["get","list","watch","create","delete","patch"]}
]'
```

---

## Step 7 — Create Argo SA token secret

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

Verify token is populated:

```bash
microk8s kubectl get secret openeo-argo-access-sa.service-account-token -n openeo -o jsonpath='{.data.token}' | base64 -d | head -c 20
```

---

## Step 8 — Deploy APISIX

APISIX acts as the API gateway protecting `/jobs`, `/files`, `/me` routes with OIDC.

```bash
microk8s helm3 repo add apisix https://charts.apiseven.com
microk8s helm3 repo update
microk8s helm3 install apisix apisix/apisix \
  --namespace ingress-apisix \
  --create-namespace \
  --set gateway.type=NodePort \
  --set ingress-controller.enabled=true
```

Then apply the OpenEO route configuration (ApisixRoute CR) from:
`argocd/eoepca/openeo-argoworkflows/parts/ingress-openeo.yaml`

Key APISIX route config:
- Protected routes: `/jobs`, `/files`, `/me`
- Plugin: `openid-connect` pointing to `https://edp-portal.eurac.edu/auth/realms/edp`
- Keycloak client: `openeo-apisix` (confidential)
- Lua pre-function for token validation

APISIX gateway NodePort: `32120` (HTTP)

---

## Step 9 — Deploy Monitoring Stack

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
  --set loki.commonConfig.replication_factor=1 \
  --set loki.storage.type=filesystem \
  --set singleBinary.replicas=1
```

### Promtail

```bash
microk8s helm3 install promtail grafana/promtail \
  --namespace monitoring \
  --set config.clients[0].url="http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1/push"
```

### Grafana

```bash
microk8s helm3 install grafana grafana/grafana \
  --namespace monitoring \
  --set adminPassword="openeo-admin" \
  --set service.type=NodePort \
  --set service.nodePort=31000 \
  --set sidecar.rules.enabled=false
```

Add Loki data source in Grafana UI:
- URL: `http://loki-gateway.monitoring.svc.cluster.local/`

---

## Step 10 — Verify deployment

```bash
# Capabilities endpoint
curl -skL https://openeo.eurac.edu/ | python3 -m json.tool | grep -E '"api_version"|"title"|"output_formats"'

# File formats
curl -skL https://openeo.eurac.edu/openeo/1.1.0/file_formats | python3 -m json.tool

# Collections count
curl -skL https://openeo.eurac.edu/openeo/1.1.0/collections | python3 -c "import sys,json; d=json.load(sys.stdin); print('Collections:', len(d['collections']))"
```

---

## Key URLs after deployment

| Service | URL |
|---------|-----|
| OpenEO API | https://openeo.eurac.edu |
| Argo Workflows UI | http://<VM_IP>:31635 |
| Kubernetes Dashboard | http://<VM_IP>:30443 |
| Grafana | http://<VM_IP>:31000 (admin / openeo-admin) |

---

## Secrets to collect before VM is lost

If you have advance warning:

```bash
# PostgreSQL password
microk8s kubectl get secret openeo-postgresql -n openeo -o jsonpath='{.data.postgres-password}' | base64 -d

# Sign key
microk8s kubectl get secret openeo-sign-key -n openeo -o jsonpath='{.data.sign-secret}' | base64 -d

# Argo SA token
microk8s kubectl get secret openeo-argo-access-sa.service-account-token -n openeo -o jsonpath='{.data.token}' | base64 -d

# PostgreSQL dump
PG_PASS=$(microk8s kubectl get secret openeo-postgresql -n openeo -o jsonpath='{.data.postgres-password}' | base64 -d)
microk8s kubectl exec -n openeo openeo-postgresql-0 -- bash -c \
  "PGPASSWORD='$PG_PASS' pg_dump -U postgres postgres" > openeo-db-backup-$(date +%Y%m%d).sql
```

---

## What lives outside this VM (safe)

- Source code → GitHub `Eurac-Research-Institute-for-EO/openeo-argoworkflows`
- Helm chart → GitHub `Eurac-Research-Institute-for-EO/charts`
- Docker images → GHCR `ghcr.io/eurac-research-institute-for-eo/`
- STAC catalog → stac.eurac.edu (Bart's server)
- Keycloak → edp-portal.eurac.edu
- EOEPCA upstream → separate cluster
