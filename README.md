# CPE Router Dashboard

Flask-based dashboard for Huawei and ZTE router cellular metrics.
  Implemented adapters for models:
Huwaei:  TBC
ZTE: MF296C

Features:

- Live cellular dashboard.
- Historical RSRP, RSRQ, and SINR charts.
- Manual speed test for latency, download, and upload metrics.
- TimescaleDB/PostgreSQL historical storage.
- Plain Kubernetes manifests under `k8s_mani/`.
- GitLab CI for checks, image build, and optional deployment.
- Existing Kubernetes Secret support.
- SOPS/Age-ready secret workflow.
- TLS ingress and LAN/external access notes.
- DNS-free LAN hostname via `router-dashboard.192.168.9.4.sslip.io`.

## Quick Deploy

Create or decrypt the Secret first, then apply the app manifests:

```bash
kubectl apply -k k8s_mani
```

The app expects this Secret to already exist:

```text
router-dashboard-secret
```

Router credentials are split by vendor:

```text
HUAWEI_ROUTER_USERNAME
HUAWEI_ROUTER_PASSWORD
ZTE_ROUTER_USERNAME
ZTE_ROUTER_PASSWORD
```

## Router Adapters

The dashboard keeps one application with router-specific code in adapters:

```text
router_clients/
  base.py
  huawei.py
  zte.py
```

Live router data is available from:

```text
/api/cellular?vendor=huawei
/api/cellular?vendor=zte
```

## Docs

- `docs/deployment.md`
- `docs/secrets.md`
- `docs/configmap.md`
- `docs/certificates-and-access.md`
- `docs/speed-tests.md`
