# Speed Tests

The dashboard can run a manual HTTP speed test from the Kubernetes pod.

Tracked metrics:

- Latency in milliseconds.
- Download throughput in Mbps.
- Upload throughput in Mbps.

Results are stored in PostgreSQL/TimescaleDB when database configuration is available.

## ConfigMap Settings

```yaml
SPEEDTEST_ENABLED: "true"
SPEEDTEST_SCHEDULE_ENABLED: "false"
SPEEDTEST_INTERVAL_SECONDS: "3600"
SPEEDTEST_RUN_ON_STARTUP: "false"
SPEEDTEST_RETENTION_DAYS: "30"
SPEEDTEST_HISTORY_LIMIT: "24"
SPEEDTEST_TIMEOUT_SECONDS: "45"
SPEEDTEST_LATENCY_ATTEMPTS: "3"
SPEEDTEST_DOWNLOAD_BYTES: "10000000"
SPEEDTEST_UPLOAD_BYTES: "2000000"
SPEEDTEST_PARALLEL_STREAMS: "4"
SPEEDTEST_SERVER_LOCATION: "Auto-detected Cloudflare edge"
SPEEDTEST_LATENCY_URL: "https://speed.cloudflare.com/cdn-cgi/trace"
SPEEDTEST_DOWNLOAD_URL: "https://speed.cloudflare.com/__down?bytes={bytes}"
SPEEDTEST_UPLOAD_URL: "https://speed.cloudflare.com/__up"
```

Hourly scheduled speed tests are supported by setting:

```yaml
SPEEDTEST_SCHEDULE_ENABLED: "true"
SPEEDTEST_INTERVAL_SECONDS: "3600"
```

Keep scheduled tests disabled unless you explicitly want them. Each default test transfers about 48 MB, so hourly tests use roughly 1.15 GB per day before protocol overhead.

The Cloudflare test endpoint exposes an edge code during the latency probe. The dashboard maps common edge codes to physical locations such as `Frankfurt, Germany`, `Milan, Italy`, or `Palermo, Italy` when available.

The dashboard runs speed tests from the Kubernetes pod, not from the browser device. It uses parallel HTTP streams to better approximate browser-based speed tests, but results can still differ from `speed.cloudflare.com` because the browser test runs on the client device and may use different measurement logic.
