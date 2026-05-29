# ConfigMap Reference

The dashboard ConfigMap is `k8s_mani/router-app_cm.yml`.

ConfigMap values are non-secret runtime settings. Passwords, usernames, session cookies, and database credentials belong in `router-dashboard-secret`, not in this ConfigMap.

## App Runtime

| Key | Current value | Meaning |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | Interface Flask binds to inside the container. Keep `0.0.0.0` for Kubernetes. |
| `APP_PORT` | `5000` | Flask listen port. Must match the Deployment container port and Service target port. |

## Dashboard Display

| Key | Current value | Meaning |
| --- | --- | --- |
| `NETWORK_TYPE` | `LTE / 4G` | Backward-compatible default display network type. |
| `OPERATOR_NAME` | `Vodafone Egypt` | Backward-compatible default operator name. |
| `DASHBOARD_NETWORK_TYPE` | `LTE / 4G` | Default network label shown w git add docs/configmap.mdhen a router does not return one. |
| `DASHBOARD_OPERATOR_NAME` | `Vodafone Egypt` | Default operator label used by Huawei and as a fallback for other routers. |

## Router Selection

| Key | Current value | Meaning |
| --- | --- | --- |
| `ROUTER_VENDOR` | `zte` | Default router selected by the dashboard and used by the historical poller. Valid values are `huawei` and `zte`. |

If the selected router is unavailable, the live cards clear and the dashboard shows a toast error. Historical charts remain visible.

The UI dropdown remains available so a user can manually query another configured router. The base Kubernetes manifest runs one dashboard replica, which is the safe default for ZTE because this ZTE CPE invalidates competing sessions. But, by introducing bounded ZTE auth retry plus `ZTE_SESSION_TTL_SECONDS` renewal so stale sessions can be refreshed and usable metrics can recover. But scale resposibly (or refine TTL) so re authing won't be excessive. 


## Huawei Router

| Key | Current value | Meaning |
| --- | --- | --- |
| `HUAWEI_ROUTER_URL` | `http://192.168.9.1` | Base URL for the Huawei CPE API. |
| `HUAWEI_ROUTER_NAME` | `Huawei` | Display name in the router switcher. |

Huawei credentials are configured in the Secret:

```text
HUAWEI_ROUTER_USERNAME
HUAWEI_ROUTER_PASSWORD
```

## ZTE Router

| Key | Current value | Meaning |
| --- | --- | --- |
| `ZTE_ROUTER_URL` | `http://192.168.9.1` | Base URL for the ZTE CPE API. |
| `ZTE_ROUTER_NAME` | `ZTE` | Display name in the router switcher. |
| `ZTE_NETWORK_TYPE` | empty | Optional display override when ZTE does not return `network_type`. Empty means use router data. |
| `ZTE_OPERATOR_NAME` | `Vodafone Egypt` | Operator label used for normalized ZTE metrics. |
| `ZTE_AUTH_MODE` | `zte_mf296c` | ZTE password transform/login mode. Current router uses MF296C SHA256 challenge behavior. |
| `ZTE_AUTH_AUTO_MAX_ATTEMPTS` | `3` | Maximum auth transforms to try when `ZTE_AUTH_MODE=auto`. Ignored for a fixed auth mode. |
| `ZTE_SESSION_TTL_SECONDS` | `300` | How long the app reuses a ZTE login session before proactively logging in again. Minimum enforced by the app is 60 seconds. |

ZTE credentials are configured in the Secret:

```text
ZTE_ROUTER_USERNAME
ZTE_ROUTER_PASSWORD
ZTE_SESSION_COOKIE
```

`ZTE_SESSION_COOKIE` should normally be empty. It is only a temporary debug override for an already signed-in browser session.

## Historical Cellular Metrics

| Key | Current value | Meaning |
| --- | --- | --- |
| `HISTORICAL_ENABLED` | `true` | Enables the background poller that inserts cellular metrics into TimescaleDB. |
| `HISTORICAL_POLL_INTERVAL_SECONDS` | `60` | How often the app polls the default router and stores a sample. Minimum enforced by the app is 5 seconds. |
| `HISTORICAL_RETENTION_DAYS` | `7` | Number of days to keep cellular rows before pruning old samples. |

Retention is applied by the app after successful inserts:

```sql
DELETE FROM cellular_metrics
WHERE time < now() - ('<days>' || ' days')::interval;
```

## Speed Test

| Key | Current value | Meaning |
| --- | --- | --- |
| `SPEEDTEST_ENABLED` | `true` | Enables manual speed tests and speed test API endpoints. |
| `SPEEDTEST_SCHEDULE_ENABLED` | `false` | Enables automatic scheduled speed tests when `true`. |
| `SPEEDTEST_INTERVAL_SECONDS` | `3600` | Interval for scheduled speed tests. Minimum enforced by the app is 300 seconds. |
| `SPEEDTEST_RUN_ON_STARTUP` | `false` | Runs one scheduled speed test when the app starts if scheduling is enabled. |
| `SPEEDTEST_RETENTION_DAYS` | `30` | Number of days to keep speed test rows before pruning old results. |
| `SPEEDTEST_HISTORY_LIMIT` | `24` | Default number of speed test rows returned by the API/UI. |
| `SPEEDTEST_TIMEOUT_SECONDS` | `45` | Timeout for each speed test HTTP operation. |
| `SPEEDTEST_LATENCY_ATTEMPTS` | `3` | Number of latency requests used to calculate latency. |
| `SPEEDTEST_DOWNLOAD_BYTES` | `10000000` | Download test size in bytes. |
| `SPEEDTEST_UPLOAD_BYTES` | `2000000` | Upload test size in bytes. |
| `SPEEDTEST_PARALLEL_STREAMS` | `4` | Number of parallel streams used for download/upload tests. |
| `SPEEDTEST_SERVER_LOCATION` | `Auto-detected Cloudflare edge` | Display label for the speed test endpoint. |
| `SPEEDTEST_LATENCY_URL` | `https://speed.cloudflare.com/cdn-cgi/trace` | URL used for latency probing. |
| `SPEEDTEST_DOWNLOAD_URL` | `https://speed.cloudflare.com/__down?bytes={bytes}` | URL template used for download tests. `{bytes}` is replaced by `SPEEDTEST_DOWNLOAD_BYTES`. |
| `SPEEDTEST_UPLOAD_URL` | `https://speed.cloudflare.com/__up` | URL used for upload tests. |

Speed test retention is applied by the app after successful inserts:

```sql
DELETE FROM speedtest_results
WHERE time < now() - ('<days>' || ' days')::interval;
```

## Database

| Key | Current value | Meaning |
| --- | --- | --- |
| `DB_HOST` | `timescaledb` | Kubernetes Service name or host for PostgreSQL/TimescaleDB. |
| `DB_PORT` | `5432` | PostgreSQL port. |
| `DB_NAME` | `router_metrics` | Database used by the dashboard. |

Database credentials are configured in the Secret:

```text
DB_USER
DB_PASSWORD
```

Changing `DB_PASSWORD` in the Secret only changes what the app sends. To change the actual PostgreSQL role password, also run:

```sql
ALTER USER router_user WITH PASSWORD '<new-password>';
```

## Applying Changes

For the default/base manifest:

```bash
kubectl apply -k k8s_mani
kubectl -n backend rollout restart deployment/router-dashboard
kubectl -n backend rollout status deployment/router-dashboard --timeout=180s
```

For optional Huawei scaling:

```bash
kubectl -n backend scale deployment/router-dashboard --replicas=2
kubectl apply -f k8s_mani/optional/router-dashboard-hpa.yml
```

To return to the safe ZTE profile:

```bash
kubectl -n backend delete hpa router-dashboard --ignore-not-found
kubectl -n backend scale deployment/router-dashboard --replicas=1
```

Most ConfigMap values are loaded only when the container starts, so restart the Deployment after plain ConfigMap changes.
