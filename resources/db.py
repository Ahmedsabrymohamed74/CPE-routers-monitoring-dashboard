import os
import re
import json
import time
from decimal import Decimal
import psycopg2
from psycopg2.extras import Json


def db_config_available():
    return all([
        os.getenv("DB_HOST"),
        os.getenv("DB_NAME"),
        os.getenv("DB_USER"),
        os.getenv("DB_PASSWORD"),
    ])


def get_conn():
    last_error = None

    for attempt in range(10):
        try:
            return psycopg2.connect(
                host=os.getenv("DB_HOST", "timescaledb"),
                port=int(os.getenv("DB_PORT", "5432")),
                dbname=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                connect_timeout=5,
            )
        except psycopg2.OperationalError as exc:
            last_error = exc
            print(f"[!] DB connection attempt {attempt + 1}/10 failed: {exc}")
            time.sleep(5)

    raise last_error

def parse_number(value):
    if value is None:
        return None

    match = re.search(r"-?\d+(\.\d+)?", str(value))
    if not match:
        return None

    return float(match.group(0))


def init_db():
    if not db_config_available():
        print("[!] DB config not available, historical storage disabled")
        return

    sql = """
    CREATE EXTENSION IF NOT EXISTS timescaledb;

    CREATE TABLE IF NOT EXISTS cellular_metrics (
        time TIMESTAMPTZ NOT NULL DEFAULT now(),

        network_type TEXT,
        operator TEXT,

        pci TEXT,
        cell_id TEXT,
        enodeb_id TEXT,

        rsrp NUMERIC,
        rsrq NUMERIC,
        sinr NUMERIC,
        rssi NUMERIC,

        band TEXT,
        earfcn TEXT,
        dl_bandwidth TEXT,
        ul_bandwidth TEXT,

        tac TEXT,
        plmn TEXT,
        transmode TEXT,
        cqi0 NUMERIC,

        raw JSONB
    );

    SELECT create_hypertable(
        'cellular_metrics',
        'time',
        if_not_exists => TRUE
    );
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)

    print("[*] DB initialized")


def insert_metric(data):
    if not db_config_available():
        return

    sql = """
    INSERT INTO cellular_metrics (
        network_type, operator,
        pci, cell_id, enodeb_id,
        rsrp, rsrq, sinr, rssi,
        band, earfcn, dl_bandwidth, ul_bandwidth,
        tac, plmn, transmode, cqi0,
        raw
    )
    VALUES (
        %(network_type)s, %(operator)s,
        %(pci)s, %(cell_id)s, %(enodeb_id)s,
        %(rsrp)s, %(rsrq)s, %(sinr)s, %(rssi)s,
        %(band)s, %(earfcn)s, %(dl_bandwidth)s, %(ul_bandwidth)s,
        %(tac)s, %(plmn)s, %(transmode)s, %(cqi0)s,
        %(raw)s
    );
    """

    row = {
        "network_type": data.get("network_type"),
        "operator": data.get("operator"),

        "pci": data.get("pci"),
        "cell_id": data.get("cell_id"),
        "enodeb_id": data.get("enodeb_id"),

        "rsrp": parse_number(data.get("rsrp")),
        "rsrq": parse_number(data.get("rsrq")),
        "sinr": parse_number(data.get("sinr")),
        "rssi": parse_number(data.get("rssi")),

        "band": data.get("band"),
        "earfcn": data.get("earfcn"),
        "dl_bandwidth": data.get("dl_bandwidth"),
        "ul_bandwidth": data.get("ul_bandwidth"),

        "tac": data.get("tac"),
        "plmn": data.get("plmn"),
        "transmode": data.get("transmode"),
        "cqi0": parse_number(data.get("cqi0")),

        "raw": Json(data.get("raw_signal", data)),
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, row)


def prune_metrics(retention_days):
    if not db_config_available() or not retention_days:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM cellular_metrics WHERE time < now() - (%s || ' days')::interval;",
                (int(retention_days),),
            )


def fetch_history(minutes=1440, limit=500, start=None, end=None):
    if not db_config_available():
        return []

    minutes = max(1, int(minutes))
    limit = min(max(1, int(limit)), 2000)

    where_clause = "time >= now() - (%s || ' minutes')::interval"
    params = [minutes]

    if start and end:
        where_clause = "time >= %s::timestamptz AND time <= %s::timestamptz"
        params = [start, end]
    elif start:
        where_clause = "time >= %s::timestamptz"
        params = [start]
    elif end:
        where_clause = "time <= %s::timestamptz"
        params = [end]

    sql = f"""
    SELECT
        time,
        network_type,
        operator,
        pci,
        cell_id,
        enodeb_id,
        rsrp,
        rsrq,
        sinr,
        rssi,
        band,
        earfcn,
        dl_bandwidth,
        ul_bandwidth,
        tac,
        plmn,
        transmode,
        cqi0
    FROM cellular_metrics
    WHERE {where_clause}
    ORDER BY time ASC;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    if len(rows) > limit and limit == 1:
        rows = [rows[-1]]
    elif len(rows) > limit:
        step = (len(rows) - 1) / (limit - 1)
        rows = [rows[round(index * step)] for index in range(limit)]

    keys = [
        "time",
        "network_type",
        "operator",
        "pci",
        "cell_id",
        "enodeb_id",
        "rsrp",
        "rsrq",
        "sinr",
        "rssi",
        "band",
        "earfcn",
        "dl_bandwidth",
        "ul_bandwidth",
        "tac",
        "plmn",
        "transmode",
        "cqi0",
    ]

    result = []
    for row in rows:
        item = dict(zip(keys, row))
        item["time"] = item["time"].isoformat()
        for key, value in list(item.items()):
            if isinstance(value, Decimal):
                item[key] = float(value)
        result.append(item)

    return result
