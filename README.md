# Router Cellular Dashboard

Flask dashboard for Huawei-style router cellular metrics.

Features:

- Live cellular dashboard.
- Historical RSRP, RSRQ, and SINR charts.
- TimescaleDB/PostgreSQL historical storage.
- Plain Kubernetes manifests under `k8s_mani/`.
- GitLab CI for checks, image build, and optional deployment.
- Existing Kubernetes Secret support.
- SOPS/Age-ready secret workflow.
- TLS ingress and LAN/external access notes.
- DNS-free LAN hostname via `router-dashboard.192.168.142.130.sslip.io`.

## Quick Deploy

Create or decrypt the Secret first, then apply the app manifests:

```bash
kubectl apply -k k8s_mani
```

The app expects this Secret to already exist:

```text
router-dashboard-secret
```

## Docs

- `docs/deployment.md`
- `docs/secrets.md`
- `docs/certificates-and-access.md`
