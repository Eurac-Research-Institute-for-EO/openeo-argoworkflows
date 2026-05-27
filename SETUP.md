# OpenEO ArgoWorkflows — VM Setup Guide

This document covers setting up a fresh Ubuntu 22.04 VM with the full OpenEO ArgoWorkflows stack from scratch. Follow the sections in order.

> **Production reference**: `eosao42.eurac.edu` (10.8.244.73), MicroK8s v1.33.9, Ubuntu 22.04.5 LTS

---

## 1. VM Prerequisites

Minimum spec (dev environment):
- Ubuntu 22.04 LTS
- 16 vCPU, 32 GB RAM (prod has 16 vCPU / 62 GB RAM)
- 200 GB disk on `/`
- Static IP, reachable by your DNS entry

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl git jq
```

---

## 2. MicroK8s

```bash
sudo snap install microk8s --classic --channel=1.33/stable
sudo usermod -aG microk8s $USER
newgrp microk8s
```

Enable addons:

```bash
microk8s enable dns
microk8s enable hostpath-storage
microk8s enable storage
microk8s enable helm
microk8s enable helm3
microk8s enable registry
microk8s enable metrics-server
microk8s enable dashboard
microk8s enable ingress
microk8s enable cert-manager
```

Set up kubectl alias (add to `~/.bashrc`):

```bash
alias kubectl='microk8s kubectl'
alias helm='microk8s helm3'
echo "alias kubectl='microk8s kubectl'" >> ~/.bashrc
echo "alias helm='microk8s helm3'" >> ~/.bashrc
source ~/.bashrc
```

Verify:

```bash
microk8s status --wait-ready
kubectl get nodes
```

---

## 3. Helm Repos

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add argo https://argoproj.github.io/argo-helm
helm repo add dask https://helm.dask.org
helm repo add apisix https://charts.apiseven.com
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
```

---

## 4. Namespaces

```bash
kubectl create namespace openeo
kubectl create namespace ingress-apisix
kubectl create namespace monitoring
kubectl create namespace cert-manager
```

---

## 5. cert-manager

```bash
helm install cert-manager cert-manager \
  --repo https://charts.jetstack.io \
  --namespace cert-manager \
  --set crds.enabled=true
```

Create a self-signed ClusterIssuer:

```bash
kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-issuer
spec:
  selfSigned: {}
EOF
```

Create a TLS certificate for the openeo namespace (replace DNS names and IP):

```bash
kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: openeo-selfsigned-cert
  namespace: openeo
spec:
  secretName: openeo-tls
  commonName: openeo-dev.eurac.edu
  dnsNames:
    - openeo-dev.eurac.edu
    - <your-hostname>
  ipAddresses:
    - <your-vm-ip>
    - 127.0.0.1
  duration: 8760h
  issuerRef:
    name: selfsigned-issuer
    kind: ClusterIssuer
EOF
```

---

## 6. APISIX

```bash
cat > /tmp/apisix-values.yaml <<EOF
gateway:
  type: NodePort
ingress-controller:
  enabled: true
  config:
    apisix:
      serviceNamespace: ingress-apisix
EOF

helm install apisix apisix/apisix \
  --version 2.12.6 \
  --namespace ingress-apisix \
  -f /tmp/apisix-values.yaml
```

---

## 7. GHCR Pull Secret

Create a GitHub Personal Access Token (PAT) with `read:packages` scope, then:

```bash
kubectl create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=<your-github-username> \
  --docker-password=<your-github-pat> \
  --docker-email=<your-email> \
  --namespace openeo
```

---

## 8. OpenEO Helm Chart

Clone the charts repo:

```bash
git clone https://github.com/Eurac-Research-Institute-for-EO/charts.git ~/charts
cd ~/charts
git remote add eurac https://github.com/Eurac-Research-Institute-for-EO/charts.git
```

Create your values file (copy from prod and adjust):

```bash
cp ~/charts/values-eurac.yaml ~/charts/values-dev.yaml
```

Key values to change in `values-dev.yaml`:

```yaml
global:
  env:
    apiDns: openeo-dev.eurac.edu        # your dev DNS name
    apiTLS: true
    apiTitle: OpenEO ArgoWorkflows Dev
    apiDescription: Development environment
    oidcUrl: https://edp-portal.eurac.edu/auth/realms/edp
    oidcOrganisation: eurac
    stacCatalogueUrl: https://stac.eurac.edu
    executorImage: ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-executor:dev
    workspaceRoot: /user_workspaces
    daskClusterTimeout: "300"           # 5 min idle timeout (not 3600)
    daskWorkerCores: 1
    daskWorkerLimit: 2
    daskWorkerMemory: 4

image:
  repository: ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-api
  tag: dev                              # dev tag, not latest
  pullPolicy: Always
```

Deploy:

```bash
helm upgrade --install openeo ~/charts/eodc/openeo-argo \
  --namespace openeo \
  -f ~/charts/values-dev.yaml \
  --wait
```

---

## 9. Ingress

Create the nginx ingress resource (replace hostname):

```bash
kubectl apply -f - <<EOF
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
    - host: openeo-dev.eurac.edu
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
        - openeo-dev.eurac.edu
      secretName: openeo-tls
EOF
```

---

## 10. Monitoring Stack

### Loki

```bash
cat > /tmp/loki-values.yaml <<EOF
deploymentMode: SingleBinary
singleBinary:
  replicas: 1
read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0
loki:
  auth_enabled: false
  commonConfig:
    replication_factor: 1
  storage:
    type: filesystem
  schemaConfig:
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: index_
          period: 24h
chunksCache:
  enabled: false
resultsCache:
  enabled: false
sidecar:
  rules:
    enabled: false
EOF

helm install loki grafana/loki \
  --version 6.55.0 \
  --namespace monitoring \
  -f /tmp/loki-values.yaml
```

### Promtail

```bash
cat > /tmp/promtail-values.yaml <<EOF
config:
  clients:
    - url: http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1/push
EOF

helm install promtail grafana/promtail \
  --version 6.17.1 \
  --namespace monitoring \
  -f /tmp/promtail-values.yaml
```

### Grafana

```bash
cat > /tmp/grafana-values.yaml <<EOF
adminPassword: openeo-admin
persistence:
  enabled: true
  size: 5Gi
service:
  type: NodePort
  nodePort: 31000
EOF

helm install grafana grafana/grafana \
  --version 10.5.15 \
  --namespace monitoring \
  -f /tmp/grafana-values.yaml
```

Access Grafana at `http://<vm-ip>:31000` (admin / openeo-admin).

Add Loki as a datasource in Grafana:
- URL: `http://loki-gateway.monitoring.svc.cluster.local/`

---

## 11. Expose Argo UI (NodePort)

The Argo UI service is ClusterIP by default. Expose it on port 31635:

```bash
kubectl patch svc openeo-argo-workflows-server -n openeo \
  -p '{"spec":{"type":"NodePort","ports":[{"port":2746,"targetPort":2746,"nodePort":31635}]}}'
```

Access at `http://<vm-ip>:31635`

Save as a script (`~/scripts/patch-argo-nodeport.sh`):

```bash
mkdir -p ~/scripts
cat > ~/scripts/patch-argo-nodeport.sh <<'EOF'
#!/bin/bash
kubectl patch svc openeo-argo-workflows-server -n openeo \
  -p '{"spec":{"type":"NodePort","ports":[{"port":2746,"targetPort":2746,"nodePort":31635}]}}'
echo "Argo UI available at http://$(hostname -I | awk '{print $1}'):31635"
EOF
chmod +x ~/scripts/patch-argo-nodeport.sh
```

> **Note**: This patch is reset on every `helm upgrade`. Re-run the script after every upgrade.

---

## 12. CI Image Tag Strategy

The GitHub Actions CI should push two tags:

| Branch | Image tag | Environment |
|--------|-----------|-------------|
| `eurac-main` | `latest` | Production (`openeo.eurac.edu`) |
| `eurac-dev` | `dev` | Dev environment |

Update `.github/workflows/build-api.yml` (or equivalent) to:

```yaml
- name: Set image tag
  run: |
    if [[ "${{ github.ref }}" == "refs/heads/eurac-main" ]]; then
      echo "TAG=latest" >> $GITHUB_ENV
    else
      echo "TAG=dev" >> $GITHUB_ENV
    fi

- name: Push image
  run: |
    docker push ghcr.io/eurac-research-institute-for-eo/openeo-argoworkflows-api:${{ env.TAG }}
```

---

## 13. Post-Deploy Verification

```bash
# All pods running
kubectl get pods -n openeo

# API responding
curl -k https://openeo-dev.eurac.edu/openeo/1.0/

# Argo UI accessible
curl http://<vm-ip>:31635

# Reconcile CronJob present
kubectl get cronjob -n openeo

# Cleanup CronJob present
kubectl get cronjob -n openeo

# Check logs
kubectl logs -n openeo -l app.kubernetes.io/name=openeo-argo --tail=20
```

---

## 14. Safe Upgrade Command (after initial install)

```bash
helm upgrade openeo ~/charts/eodc/openeo-argo \
  --namespace openeo \
  -f ~/charts/values-dev.yaml \
  --set image.pullPolicy=Always \
  --wait

~/scripts/patch-argo-nodeport.sh
```

**Rule**: Always edit `values-dev.yaml`, then re-run upgrade. Never use long `--set` chains.

---

## Key Differences: Dev vs Production

| Setting | Production | Dev |
|---------|-----------|-----|
| DNS | `openeo.eurac.edu` | `openeo-dev.eurac.edu` |
| Image tag | `latest` | `dev` |
| Helm values | `values-eurac.yaml` | `values-dev.yaml` |
| Dask idle timeout | 300s | 300s |
| Worker memory | 4 GB | 4 GB |
| Argo UI port | 31635 | 31635 |
| Grafana port | 31000 | 31000 |
