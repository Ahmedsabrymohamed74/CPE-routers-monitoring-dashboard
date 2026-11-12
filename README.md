# CPE Router Dashboard

Flask-based dashboard for Huawei and ZTE router cellular metrics.
  Implemented adapters for models:
Huwaei:  H153-381
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

## Architecture

Kubernetes architecture diagrams for this repository are generated from the local manifests with KubeDiagrams.

![Kubernetes architecture](docs/diagrams/full-cluster.svg)

## Quick Deploy

Dashboard access requires login. Before deploying an image with account support, configure
`DASHBOARD_SECRET_KEY`, `DASHBOARD_ADMIN_USERNAME`, and `DASHBOARD_ADMIN_PASSWORD` in the existing
Kubernetes Secret. See [Dashboard accounts](docs/authentication.md) for account requests, admin
approval, password changes, and password recovery when the UI is unavailable.

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

- [Dashboard accounts and recovery](docs/authentication.md)

- `docs/deployment.md`
- `docs/secrets.md`
- `docs/configmap.md`
- `docs/certificates-and-access.md`
- `docs/speed-tests.md`

## Kubernetes Diagrams

KubeDiagrams is used here to render architecture diagrams from the Kubernetes manifests under `k8s_mani/`. The generator intentionally uses only explicit non-secret manifest paths; it does not include real Secret manifests, router credentials, database passwords, TLS keys, or private certificate material.

Prerequisites:

- Linux shell environment.
- Python 3.9 or newer.
- Graphviz `dot`.
- KubeDiagrams CLI, which provides `kube-diagrams`.

Install KubeDiagrams for the current user:

```bash
python3 -m pip install --user KubeDiagrams
```

If `python3 -m pip` is unavailable on the VM, bootstrap user-local pip first:

```bash
python3 -m ensurepip --user
python3 -m pip install --user KubeDiagrams
```

Install Graphviz if `dot` is not already available. For example, on Fedora/RHEL-like systems:

```bash
sudo dnf install graphviz
```

Generate diagrams:

```bash
scripts/generate-kube-diagrams.sh
```

Generated files are written to:

```text
docs/diagrams/
```

Expected SVG outputs:

```text
docs/diagrams/backend.svg
docs/diagrams/full-cluster.svg
```

The script also generates optional diagrams when matching non-secret manifests exist:

```text
docs/diagrams/database.svg
docs/diagrams/traefik.svg
docs/diagrams/monitoring.svg
```

If the installed KubeDiagrams version supports Mermaid output, the script also writes `.mmd` files next to the SVG files. Missing optional formats do not fail the run.

### Cellular history profiles

See [cell/sector filtering, chart profile tooltips, loading behavior and tests](docs/history-profiles.md) for issues #25–#27.
