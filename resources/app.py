import os
import time
import threading
from threading import Lock

from flask import Flask, jsonify, render_template, request

from router_client import RouterClient

try:
    from db import (
        fetch_history,
        init_db,
        insert_metric,
        prune_metrics,
        db_config_available,
    )
except ImportError:
    init_db = None
    insert_metric = None
    prune_metrics = None
    fetch_history = None

    def db_config_available():
        return False


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


ROUTER_URL = os.getenv("ROUTER_URL", "http://192.168.9.1")
ROUTER_USERNAME = os.getenv("ROUTER_USERNAME", "admin")
ROUTER_PASSWORD = os.getenv("ROUTER_PASSWORD")

NETWORK_TYPE = env_value("DASHBOARD_NETWORK_TYPE", "LTE / 4G", aliases=("NETWORK_TYPE",))
OPERATOR_NAME = env_value("DASHBOARD_OPERATOR_NAME", "Vodafone Egypt", aliases=("OPERATOR_NAME",))

POLL_INTERVAL_SECONDS = max(
    5,
    env_int("HISTORICAL_POLL_INTERVAL_SECONDS", 60, aliases=("POLL_INTERVAL_SECONDS",)),
)
ENABLE_HISTORICAL = env_bool("HISTORICAL_ENABLED", True, aliases=("ENABLE_HISTORICAL",))
HISTORICAL_RETENTION_DAYS = env_int("HISTORICAL_RETENTION_DAYS", 7)

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = env_int("APP_PORT", 5000)


if not ROUTER_PASSWORD:
    raise RuntimeError("ROUTER_PASSWORD environment variable is required")


app = Flask(__name__)

router = RouterClient(
    router_url=ROUTER_URL,
    username=ROUTER_USERNAME,
    password=ROUTER_PASSWORD,
)

router_lock = Lock()


# --------------------
# Helpers
# --------------------
def clean_value(value):
    if value is None or value == "":
        return None
    return value


def build_cellular_payload():
    """
    Fetch live signal data from the router and normalize it into the dashboard/API format.
    """
    with router_lock:
        signal = router.get_signal()

    return {
        "network_type": NETWORK_TYPE,
        "operator": OPERATOR_NAME,

        "pci": clean_value(signal.get("pci")),
        "cell_id": clean_value(signal.get("cell_id")),
        "enodeb_id": clean_value(signal.get("enodeb_id")),

        "rsrp": clean_value(signal.get("rsrp")),
        "rsrq": clean_value(signal.get("rsrq")),
        "sinr": clean_value(signal.get("sinr")),
        "rssi": clean_value(signal.get("rssi")),

        "band": clean_value(signal.get("band")),
        "band_info": clean_value(signal.get("bandInfo")),
        "earfcn": clean_value(signal.get("earfcn")),
        "ul_bandwidth": clean_value(signal.get("ulbandwidth")),
        "dl_bandwidth": clean_value(signal.get("dlbandwidth")),

        "tac": clean_value(signal.get("tac")),
        "plmn": clean_value(signal.get("plmn")),
        "rrc_status": clean_value(signal.get("rrc_status")),
        "txpower": clean_value(signal.get("txpower")),
        "transmode": clean_value(signal.get("transmode")),
        "cqi0": clean_value(signal.get("cqi0")),

        "raw_signal": signal,
    }


def historical_poller():
    """
    Background polling loop that stores snapshots in TimescaleDB/PostgreSQL.
    """
    db_ready = False
    print(f"[*] Historical poller started. Interval={POLL_INTERVAL_SECONDS}s", flush=True)

    while True:
        try:
            if not db_ready and init_db:
                init_db()
                db_ready = True
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
        "router_url": ROUTER_URL,
        "historical_enabled": ENABLE_HISTORICAL,
        "historical_poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "historical_retention_days": HISTORICAL_RETENTION_DAYS,
        "db_config_available": db_config_available(),
    })


@app.route("/api/cellular")
def cellular():
    try:
        return jsonify(build_cellular_payload())
    except Exception as exc:
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
        return jsonify({
            "minutes": int(minutes),
            "limit": int(limit),
            "start": start,
            "end": end,
            "items": fetch_history(minutes=minutes, limit=limit, start=start, end=end),
        })
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
    print(f"[*] Router URL: {ROUTER_URL}", flush=True)
    print(f"[*] Network Type: {NETWORK_TYPE}", flush=True)
    print(f"[*] Operator: {OPERATOR_NAME}", flush=True)
    print(f"[*] Historical enabled: {ENABLE_HISTORICAL}", flush=True)
    print(f"[*] Historical interval: {POLL_INTERVAL_SECONDS}s", flush=True)
    print(f"[*] Historical retention: {HISTORICAL_RETENTION_DAYS}d", flush=True)

    start_historical_if_enabled()

    app.run(
        host=APP_HOST,
        port=APP_PORT,
        debug=False,
    )
