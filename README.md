# CPE Router Dashboard

Flask dashboard for Huawei and ZTE router cellular metrics.

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

## Future Router Adapters

The next phase is to keep one dashboard app and move router-specific code into adapters:

```text
router_clients/
  base.py
  huawei.py
  zte.py
```

ZTE live fetch support uses `GET /goform/goform_get_cmd_process` on `http://192.168.9.1` after the app logs in with `ZTE_ROUTER_PASSWORD`. Initial normalization maps `network_type`, `lte_rsrp`/`Z5g_rsrp`, `sinr`/`Z5g_SINR`/`Z5g_snr`, `rssi`, `Z_PCI`/`Z5g_PCI`, `cell_id`/`Z5g_CELL_ID`, `ZCELLINFO_band`/`Z5g_CELLINFO_band`, and LTE CA fields into the existing dashboard model.

ZTE login can also use `ZTE_ROUTER_PASSWORD`; for the MF296C firmware at `192.168.9.1`, `ZTE_AUTH_MODE=zte_mf296c` posts `goformId=LOGIN` with `SHA256(SHA256(password) + LD)`. `ZTE_AUTH_MODE=auto` can try other common ZTE transforms, limited by `ZTE_AUTH_AUTO_MAX_ATTEMPTS`.

`ZTE_SESSION_COOKIE` is only a temporary debug override for an already signed-in browser session. It is not the normal authentication path.

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
