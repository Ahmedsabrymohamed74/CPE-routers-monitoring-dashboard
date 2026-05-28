# Deployment

This repo is prepared for GitLab source control, GitLab CI, and plain Kubernetes deployment with `kubectl`.

## Build And Push

GitLab CI builds the image from `resources/` and pushes it to the GitLab container registry:

```bash
registry.gitlab.example.com/<group>/<project>/router-dashboard:<commit>
```

For the current lab registry:

```bash
podman build -t registry.lab.local:5000/router-dashboard:latest resources
podman push registry.lab.local:5000/router-dashboard:latest
```

## Kubernetes Deploy

Use the checked-in manifests:

```bash
kubectl apply -k k8s_mani
kubectl -n backend rollout status deployment/router-dashboard --timeout=180s
```

Scaling overlays are available when you want the Kubernetes replica policy to match the router you are currently relying on:

```bash
# Single-replica profile: use for ZTE because ZTE CPE sessions invalidate each other.
kubectl apply -k k8s_overlays/single-replica
kubectl -n backend delete hpa router-dashboard --ignore-not-found

# Scalable profile: use for Huawei. Starts with two replicas and includes an HPA.
kubectl apply -k k8s_overlays/scalable
```

Prerequisites:

- Namespace `backend`.
- Existing Kubernetes Secret `router-dashboard-secret`.
- Existing TimescaleDB/PostgreSQL service reachable as `timescaledb:5432`.
- Existing TLS Secret `router-dashboard-tls` if TLS ingress is enabled.
- Traefik ingress controller for `ingressClassName: traefik`.

If deploying a GitLab-built image manually:

```bash
kubectl -n backend set image deployment/router-dashboard \
  router-dashboard=registry.gitlab.example.com/<group>/<project>/router-dashboard:<tag>
```

## GitLab Deploy

The manual `deploy-lab` job:

1. Decrypts `secrets/router-dashboard-secret.sops.yaml` if present.
2. Runs `kubectl apply -k k8s_mani`.
3. Updates the deployment image to the commit tag.
4. Waits for rollout.

Required GitLab CI variables:

```text
KUBECONFIG
SOPS_AGE_KEY   # only needed if using encrypted SOPS secrets
```

## LAN Access

The dashboard app Service is `ClusterIP`; Traefik owns the LAN NodePort:

```text
http://<node-ip>:30080
```

For hostname access from phones or other LAN devices:

1. Ensure the device can route to the Kubernetes node IP.
2. Configure LAN DNS so `router-dashboard.lab.local` points to the node/ingress address.
3. Open `http://router-dashboard.lab.local:30080` or the HTTPS ingress endpoint, depending on Traefik/TLS routing.

## External Access

Do not expose the Flask app directly to the internet.

Preferred options:

- VPN into the lab network.
- Cloudflare Tunnel or similar authenticated tunnel.
- Reverse proxy with TLS and an authentication layer.
- Ingress plus external DNS only if access is protected.
