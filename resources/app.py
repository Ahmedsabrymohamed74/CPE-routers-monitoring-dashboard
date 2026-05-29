import os
import time
import threading
from threading import Lock

from flask import Flask, jsonify, render_template, request

from router_clients import create_router_client

try:
    from db import (
        fetch_cell_tags,
        fetch_history,
        fetch_history_cell_groups,
        fetch_speedtest_results,
        init_db,
        insert_metric,
        insert_speedtest_result,
        prune_metrics,
        prune_speedtest_results,
        upsert_cell_tag,
        db_config_available,
    )
except ImportError:
    fetch_cell_tags = None
    init_db = None
    insert_metric = None
    prune_metrics = None
    fetch_history = None
    fetch_history_cell_groups = None
    insert_speedtest_result = None
    fetch_speedtest_results = None
    prune_speedtest_results = None
    upsert_cell_tag = None

    def db_config_available():
        return False

from speedtest_runner import run_speedtest


# --------------------
# App configuration
# --------------------
def env_value(name, default=None, aliases=()):
    for key in (name, *aliases):
        value = os.getenv(key)
        if value not in (None, ""):
            return value
    return default


def env_bool(name, default=False, aliases=()):
    value = env_value(name, aliases=aliases)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default, aliases=()):
    value = env_value(name, aliases=aliases)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        print(f"[!] Invalid integer for {name}={value!r}; using {default}", flush=True)
        return default


ROUTER_VENDOR = env_value("ROUTER_VENDOR", "huawei").strip().lower()
HUAWEI_ROUTER_URL = env_value("HUAWEI_ROUTER_URL", "http://192.168.9.1", aliases=("ROUTER_URL",))
HUAWEI_ROUTER_NAME = env_value("HUAWEI_ROUTER_NAME", "Huawei", aliases=("ROUTER_NAME",))
HUAWEI_ROUTER_USERNAME = env_value("HUAWEI_ROUTER_USERNAME", "admin", aliases=("ROUTER_USERNAME",))
HUAWEI_ROUTER_PASSWORD = env_value("HUAWEI_ROUTER_PASSWORD", aliases=("ROUTER_PASSWORD",))
ZTE_ROUTER_URL = env_value("ZTE_ROUTER_URL", "http://192.168.9.1")
ZTE_ROUTER_NAME = env_value("ZTE_ROUTER_NAME", "ZTE")
ZTE_ROUTER_USERNAME = env_value("ZTE_ROUTER_USERNAME", aliases=("ZTE_USERNAME",))
ZTE_ROUTER_PASSWORD = env_value("ZTE_ROUTER_PASSWORD", aliases=("ZTE_PASSWORD",))
ZTE_SESSION_COOKIE = env_value("ZTE_SESSION_COOKIE", aliases=("ZTE_ZSIDN",))
ZTE_AUTH_MODE = env_value("ZTE_AUTH_MODE", "zte_mf296c")
ZTE_AUTH_AUTO_MAX_ATTEMPTS = env_int("ZTE_AUTH_AUTO_MAX_ATTEMPTS", 3)
ZTE_SESSION_TTL_SECONDS = max(60, env_int("ZTE_SESSION_TTL_SECONDS", 300))

NETWORK_TYPE = env_value("DASHBOARD_NETWORK_TYPE", "LTE / 4G", aliases=("NETWORK_TYPE",))
OPERATOR_NAME = env_value("DASHBOARD_OPERATOR_NAME", "Vodafone Egypt", aliases=("OPERATOR_NAME",))

POLL_INTERVAL_SECONDS = max(
    5,
    env_int("HISTORICAL_POLL_INTERVAL_SECONDS", 60, aliases=("POLL_INTERVAL_SECONDS",)),
)
ENABLE_HISTORICAL = env_bool("HISTORICAL_ENABLED", True, aliases=("ENABLE_HISTORICAL",))
HISTORICAL_RETENTION_DAYS = env_int("HISTORICAL_RETENTION_DAYS", 7)

SPEEDTEST_ENABLED = env_bool("SPEEDTEST_ENABLED", True)
SPEEDTEST_SCHEDULE_ENABLED = env_bool("SPEEDTEST_SCHEDULE_ENABLED", False)
SPEEDTEST_INTERVAL_SECONDS = max(300, env_int("SPEEDTEST_INTERVAL_SECONDS", 3600))
SPEEDTEST_RUN_ON_STARTUP = env_bool("SPEEDTEST_RUN_ON_STARTUP", False)
SPEEDTEST_RETENTION_DAYS = env_int("SPEEDTEST_RETENTION_DAYS", 30)
SPEEDTEST_HISTORY_LIMIT = env_int("SPEEDTEST_HISTORY_LIMIT", 24)
SPEEDTEST_TIMEOUT_SECONDS = max(5, env_int("SPEEDTEST_TIMEOUT_SECONDS", 45))
SPEEDTEST_LATENCY_ATTEMPTS = max(1, env_int("SPEEDTEST_LATENCY_ATTEMPTS", 3))
SPEEDTEST_DOWNLOAD_BYTES = max(1_000_000, env_int("SPEEDTEST_DOWNLOAD_BYTES", 10_000_000))
SPEEDTEST_UPLOAD_BYTES = max(250_000, env_int("SPEEDTEST_UPLOAD_BYTES", 2_000_000))
SPEEDTEST_PARALLEL_STREAMS = max(1, env_int("SPEEDTEST_PARALLEL_STREAMS", 4))
SPEEDTEST_LATENCY_URL = env_value(
    "SPEEDTEST_LATENCY_URL",
    "https://speed.cloudflare.com/cdn-cgi/trace",
)
SPEEDTEST_DOWNLOAD_URL = env_value(
    "SPEEDTEST_DOWNLOAD_URL",
    "https://speed.cloudflare.com/__down?bytes={bytes}",
)
SPEEDTEST_UPLOAD_URL = env_value(
    "SPEEDTEST_UPLOAD_URL",
    "https://speed.cloudflare.com/__up",
)
SPEEDTEST_SERVER_LOCATION = env_value("SPEEDTEST_SERVER_LOCATION", "Auto-detected Cloudflare edge")

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = env_int("APP_PORT", 5000)


if ROUTER_VENDOR not in {"huawei", "zte"}:
    raise RuntimeError("ROUTER_VENDOR must be either 'huawei' or 'zte'")

if ROUTER_VENDOR == "huawei" and not HUAWEI_ROUTER_PASSWORD:
    raise RuntimeError("HUAWEI_ROUTER_PASSWORD or ROUTER_PASSWORD environment variable is required")

if ROUTER_VENDOR == "zte" and not (ZTE_ROUTER_PASSWORD or ZTE_SESSION_COOKIE):
    raise RuntimeError("ZTE_ROUTER_PASSWORD or ZTE_SESSION_COOKIE environment variable is required")


app = Flask(__name__)

routers = {
    "huawei": create_router_client(
        "huawei",
        router_url=HUAWEI_ROUTER_URL,
        username=HUAWEI_ROUTER_USERNAME,
        password=HUAWEI_ROUTER_PASSWORD,
        router_name=HUAWEI_ROUTER_NAME,
    ),
    "zte": create_router_client(
        "zte",
        router_url=ZTE_ROUTER_URL,
        username=ZTE_ROUTER_USERNAME,
        password=ZTE_ROUTER_PASSWORD,
        session_cookie=ZTE_SESSION_COOKIE,
        auth_mode=ZTE_AUTH_MODE,
        auto_max_attempts=ZTE_AUTH_AUTO_MAX_ATTEMPTS,
        session_ttl_seconds=ZTE_SESSION_TTL_SECONDS,
        router_name=ZTE_ROUTER_NAME,
    ),
}

router_lock = Lock()
speedtest_lock = Lock()
db_init_lock = Lock()
db_initialized = False
latest_speedtest_result = None

ISO_3166_2_REGIONS = [
    {"code": "EG-ALX", "name": "Alexandria"},
    {"code": "EG-ASN", "name": "Aswan"},
    {"code": "EG-AST", "name": "Asyut"},
    {"code": "EG-BA", "name": "Red Sea"},
    {"code": "EG-BH", "name": "Beheira"},
    {"code": "EG-BNS", "name": "Beni Suef"},
    {"code": "EG-C", "name": "Cairo"},
    {"code": "EG-DK", "name": "Dakahlia"},
    {"code": "EG-DT", "name": "Damietta"},
    {"code": "EG-FYM", "name": "Faiyum"},
    {"code": "EG-GH", "name": "Gharbia"},
    {"code": "EG-GZ", "name": "Giza"},
    {"code": "EG-IS", "name": "Ismailia"},
    {"code": "EG-JS", "name": "South Sinai"},
    {"code": "EG-KB", "name": "Qalyubia"},
    {"code": "EG-KFS", "name": "Kafr el-Sheikh"},
    {"code": "EG-KN", "name": "Qena"},
    {"code": "EG-LX", "name": "Luxor"},
    {"code": "EG-MN", "name": "Minya"},
    {"code": "EG-MNF", "name": "Monufia"},
    {"code": "EG-MT", "name": "Matrouh"},
    {"code": "EG-PTS", "name": "Port Said"},
    {"code": "EG-SHG", "name": "Sohag"},
    {"code": "EG-SHR", "name": "Sharqia"},
    {"code": "EG-SIN", "name": "North Sinai"},
    {"code": "EG-SUZ", "name": "Suez"},
    {"code": "EG-WAD", "name": "New Valley"},
]
ISO_3166_2_REGION_CODES = {region["code"] for region in ISO_3166_2_REGIONS}


# --------------------
# Helpers
# --------------------
def clean_value(value):
    if value is None or value == "":
        return None
    return value


def configured_router_vendor():
    return ROUTER_VENDOR


def router_config(vendor):
    normalized_vendor = vendor.strip().lower()
    if normalized_vendor == "huawei":
        return {
            "operator_name": OPERATOR_NAME,
            "network_type": NETWORK_TYPE,
        }

    if normalized_vendor == "zte":
        return {
            "operator_name": env_value("ZTE_OPERATOR_NAME", OPERATOR_NAME),
            "network_type": env_value("ZTE_NETWORK_TYPE", None),
        }

    raise ValueError(f"Unsupported router vendor: {vendor}")


def get_router(vendor=None):
    normalized_vendor = (vendor or configured_router_vendor()).strip().lower()
    router_client = routers.get(normalized_vendor)
    if not router_client:
        raise ValueError(f"Unsupported router vendor: {normalized_vendor}")

    return normalized_vendor, router_client


def build_cellular_payload(vendor=None):
    """
    Fetch live signal data from the router and normalize it into the dashboard/API format.
    """
    normalized_vendor, router_client = get_router(vendor)
    config = router_config(normalized_vendor)

    with router_lock:
        payload = router_client.build_payload(**config)

    apply_cell_tag(payload)
    return payload


def apply_cell_tag(payload):
    cell_id = payload.get("cell_id")
    payload["cell_tag"] = None
    payload["iso_region_code"] = None
    payload["custom_label"] = None
    payload["cell_label"] = str(cell_id) if cell_id else None

    if not fetch_cell_tags or not db_config_available():
        return payload

    if not cell_id:
        return payload

    try:
        ensure_db_initialized()
        tags = fetch_cell_tags([cell_id])
    except Exception as exc:
        print(f"[!] Cell tag lookup error for cell_id={cell_id!r}: {exc}", flush=True)
        return payload

    if tags:
        tag = tags[0]
        payload["cell_tag"] = tag.get("cell_tag")
        payload["iso_region_code"] = tag.get("iso_region_code")
        payload["custom_label"] = tag.get("custom_label")
        payload["cell_label"] = tag.get("cell_tag")
    else:
        payload["cell_tag"] = None
        payload["iso_region_code"] = None
        payload["custom_label"] = None
        payload["cell_label"] = str(cell_id)

    return payload


def speedtest_config():
    return {
        "latency_url": SPEEDTEST_LATENCY_URL,
        "download_url": SPEEDTEST_DOWNLOAD_URL,
        "upload_url": SPEEDTEST_UPLOAD_URL,
        "latency_attempts": SPEEDTEST_LATENCY_ATTEMPTS,
        "download_bytes": SPEEDTEST_DOWNLOAD_BYTES,
        "upload_bytes": SPEEDTEST_UPLOAD_BYTES,
        "parallel_streams": SPEEDTEST_PARALLEL_STREAMS,
        "timeout_seconds": SPEEDTEST_TIMEOUT_SECONDS,
    }


def ensure_db_initialized():
    global db_initialized

    if db_initialized or not init_db or not db_config_available():
        return

    with db_init_lock:
        if db_initialized:
            return
        init_db()
        db_initialized = True


def store_speedtest_result(result):
    if not insert_speedtest_result:
        return

    ensure_db_initialized()
    if not db_config_available():
        return

    insert_speedtest_result(result)
    if prune_speedtest_results and SPEEDTEST_RETENTION_DAYS > 0:
        prune_speedtest_results(SPEEDTEST_RETENTION_DAYS)


def run_speedtest_job(source="manual"):
    global latest_speedtest_result

    if not SPEEDTEST_ENABLED:
        raise RuntimeError("Speed test is disabled")

    if not speedtest_lock.acquire(blocking=False):
        raise RuntimeError("Speed test already running")

    try:
        print(f"[*] Starting {source} speed test", flush=True)
        result = run_speedtest(speedtest_config())
        result["source"] = source
        result["server_location"] = result.get("server_location") or SPEEDTEST_SERVER_LOCATION
        latest_speedtest_result = result
        store_speedtest_result(result)
        print(
            "[*] Speed test complete: "
            f"latency={result['latency_ms']}ms "
            f"down={result['download_mbps']}Mbps "
            f"up={result['upload_mbps']}Mbps",
            flush=True,
        )
        return result
    finally:
        speedtest_lock.release()


def speedtest_poller():
    print(f"[*] Speed test scheduler started. Interval={SPEEDTEST_INTERVAL_SECONDS}s", flush=True)

    if not SPEEDTEST_RUN_ON_STARTUP:
        time.sleep(SPEEDTEST_INTERVAL_SECONDS)

    while True:
        try:
            run_speedtest_job(source="scheduled")
        except Exception as exc:
            print(f"[!] Scheduled speed test error: {exc}", flush=True)

        time.sleep(SPEEDTEST_INTERVAL_SECONDS)


def historical_poller():
    """
    Background polling loop that stores snapshots in TimescaleDB/PostgreSQL.
    """
    db_ready = False
    print(f"[*] Historical poller started. Interval={POLL_INTERVAL_SECONDS}s", flush=True)

    while True:
        try:
            if not db_ready and init_db:
                ensure_db_initialized()
                db_ready = db_initialized
                print("[*] Historical database initialized", flush=True)

            data = build_cellular_payload()

            if insert_metric:
                insert_metric(data)
                print("[*] Historical metric inserted", flush=True)
                if prune_metrics and HISTORICAL_RETENTION_DAYS > 0:
                    prune_metrics(HISTORICAL_RETENTION_DAYS)
            else:
                print("[!] insert_metric unavailable; historical insert skipped", flush=True)

        except Exception as exc:
            db_ready = False
            print(f"[!] Historical polling error: {exc}", flush=True)

        time.sleep(POLL_INTERVAL_SECONDS)


def start_historical_if_enabled():
    if not ENABLE_HISTORICAL:
        print("[*] Historical polling disabled", flush=True)
        return

    if not db_config_available():
        print("[!] DB config not available; historical polling disabled", flush=True)
        return

    thread = threading.Thread(
        target=historical_poller,
        daemon=True,
    )
    thread.start()


def start_speedtest_scheduler_if_enabled():
    if not SPEEDTEST_ENABLED:
        print("[*] Speed tests disabled", flush=True)
        return

    if not SPEEDTEST_SCHEDULE_ENABLED:
        print("[*] Scheduled speed tests disabled", flush=True)
        return

    thread = threading.Thread(
        target=speedtest_poller,
        daemon=True,
    )
    thread.start()


# --------------------
# Routes
# --------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "router_vendor": ROUTER_VENDOR,
        "router_url": routers[ROUTER_VENDOR].router_url,
        "historical_enabled": ENABLE_HISTORICAL,
        "historical_poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "historical_retention_days": HISTORICAL_RETENTION_DAYS,
        "db_config_available": db_config_available(),
        "speedtest_enabled": SPEEDTEST_ENABLED,
        "speedtest_schedule_enabled": SPEEDTEST_SCHEDULE_ENABLED,
        "speedtest_interval_seconds": SPEEDTEST_INTERVAL_SECONDS,
        "speedtest_server_location": SPEEDTEST_SERVER_LOCATION,
    })


@app.route("/api/routers")
def router_list():
    return jsonify({
        "default_vendor": configured_router_vendor(),
        "switch_enabled": True,
        "routers": [
            {
                "vendor": "huawei",
                "name": routers["huawei"].router_name,
                "url": routers["huawei"].router_url,
                "configured": bool(HUAWEI_ROUTER_PASSWORD),
                "enabled": True,
                "disabled_reason": None,
            },
            {
                "vendor": "zte",
                "name": routers["zte"].router_name,
                "url": routers["zte"].router_url,
                "configured": bool(ZTE_ROUTER_PASSWORD or routers["zte"].session_cookie),
                "enabled": True,
                "disabled_reason": None,
            },
        ],
    })


@app.route("/api/cellular")
def cellular():
    vendor = request.args.get("vendor") or configured_router_vendor()
    try:
        return jsonify(build_cellular_payload(vendor))
    except Exception as exc:
        print(
            f"[!] Cellular fetch error vendor={vendor!r} type={type(exc).__name__}: {exc}",
            flush=True,
        )
        return jsonify({
            "error": True,
            "message": str(exc),
        }), 500


@app.route("/api/iso-regions")
def iso_regions():
    return jsonify({"items": ISO_3166_2_REGIONS})


@app.route("/api/cell-tags", methods=["GET"])
def cell_tags():
    if not fetch_cell_tags:
        return jsonify({"error": True, "message": "cell tag storage unavailable"}), 503

    try:
        ensure_db_initialized()
        cell_id = request.args.get("cell_id")
        cell_ids = [cell_id] if cell_id else None
        return jsonify({"items": fetch_cell_tags(cell_ids)})
    except Exception as exc:
        print(f"[!] Cell tag fetch error: {exc}", flush=True)
        return jsonify({
            "error": True,
            "message": str(exc),
        }), 500


@app.route("/api/cell-tags", methods=["POST"])
def save_cell_tag():
    if not upsert_cell_tag:
        return jsonify({"error": True, "message": "cell tag storage unavailable"}), 503

    payload = request.get_json(silent=True) or {}
    cell_id = str(payload.get("cell_id") or "").strip()
    iso_region_code = str(payload.get("iso_region_code") or "").strip()
    custom_label = str(payload.get("custom_label") or "").strip()
    notes = str(payload.get("notes") or "").strip()

    if not cell_id:
        return jsonify({"error": True, "message": "cell_id is required"}), 400
    if iso_region_code not in ISO_3166_2_REGION_CODES:
        return jsonify({"error": True, "message": "Choose a valid ISO 3166-2 region code"}), 400

    try:
        ensure_db_initialized()
        upsert_cell_tag(
            cell_id=cell_id,
            iso_region_code=iso_region_code,
            custom_label=custom_label or None,
            notes=notes or None,
        )
        items = fetch_cell_tags([cell_id]) if fetch_cell_tags else []
        return jsonify({"saved": True, "item": items[0] if items else None})
    except Exception as exc:
        print(f"[!] Cell tag save error: {exc}", flush=True)
        return jsonify({
            "error": True,
            "message": str(exc),
        }), 500


@app.route("/api/history")
def history():
    if not fetch_history:
        return jsonify({"error": True, "message": "historical storage unavailable"}), 503

    try:
        minutes = request.args.get("minutes", "1440")
        limit = request.args.get("limit", "500")
        start = request.args.get("start")
        end = request.args.get("end")
        cell_id = request.args.get("cell_id")
        ensure_db_initialized()
        return jsonify({
            "minutes": int(minutes),
            "limit": int(limit),
            "start": start,
            "end": end,
            "cell_id": cell_id,
            "items": fetch_history(
                minutes=minutes,
                limit=limit,
                start=start,
                end=end,
                cell_id=cell_id,
            ),
            "cell_groups": fetch_history_cell_groups(
                minutes=minutes,
                start=start,
                end=end,
            ) if fetch_history_cell_groups else [],
        })
    except Exception as exc:
        print(f"[!] Historical fetch error: {exc}", flush=True)
        return jsonify({
            "error": True,
            "message": str(exc),
        }), 500


@app.route("/api/speedtest", methods=["GET"])
def speedtest_status():
    items = []
    minutes = request.args.get("minutes", "1440")
    limit = request.args.get("limit", str(SPEEDTEST_HISTORY_LIMIT))
    start = request.args.get("start")
    end = request.args.get("end")

    if fetch_speedtest_results and db_config_available():
        try:
            ensure_db_initialized()
            items = fetch_speedtest_results(
                minutes=minutes,
                limit=limit,
                start=start,
                end=end,
            )
        except Exception as exc:
            print(f"[!] Speed test history fetch error: {exc}", flush=True)

    latest = latest_speedtest_result or (items[0] if items else None)

    return jsonify({
        "enabled": SPEEDTEST_ENABLED,
        "schedule_enabled": SPEEDTEST_SCHEDULE_ENABLED,
        "interval_seconds": SPEEDTEST_INTERVAL_SECONDS,
        "server_location": SPEEDTEST_SERVER_LOCATION,
        "minutes": int(minutes),
        "limit": int(limit),
        "start": start,
        "end": end,
        "running": speedtest_lock.locked(),
        "latest": latest,
        "items": items,
    })


@app.route("/api/speedtest/run", methods=["POST"])
def speedtest_run():
    try:
        return jsonify(run_speedtest_job(source="manual"))
    except RuntimeError as exc:
        status_code = 409 if "already running" in str(exc) else 400
        return jsonify({
            "error": True,
            "message": str(exc),
        }), status_code
    except Exception as exc:
        return jsonify({
            "error": True,
            "message": str(exc),
        }), 500


# --------------------
# Main
# --------------------
if __name__ == "__main__":
    print("[*] Starting router dashboard", flush=True)
    print(f"[*] Default router vendor: {ROUTER_VENDOR}", flush=True)
    if ROUTER_VENDOR == "zte":
        print("[*] ZTE session renewal enabled; keep replica count reasonable", flush=True)
    print(f"[*] Huawei Router URL: {HUAWEI_ROUTER_URL}", flush=True)
    print(f"[*] ZTE Router URL: {ZTE_ROUTER_URL}", flush=True)
    print(f"[*] Network Type: {NETWORK_TYPE}", flush=True)
    print(f"[*] Operator: {OPERATOR_NAME}", flush=True)
    print(f"[*] Historical enabled: {ENABLE_HISTORICAL}", flush=True)
    print(f"[*] Historical interval: {POLL_INTERVAL_SECONDS}s", flush=True)
    print(f"[*] Historical retention: {HISTORICAL_RETENTION_DAYS}d", flush=True)
    print(f"[*] Speed tests enabled: {SPEEDTEST_ENABLED}", flush=True)
    print(f"[*] Scheduled speed tests enabled: {SPEEDTEST_SCHEDULE_ENABLED}", flush=True)
    print(f"[*] Speed test interval: {SPEEDTEST_INTERVAL_SECONDS}s", flush=True)

    start_historical_if_enabled()
    start_speedtest_scheduler_if_enabled()

    app.run(
        host=APP_HOST,
        port=APP_PORT,
        debug=False,
    )
