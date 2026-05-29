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

    CREATE TABLE IF NOT EXISTS speedtest_results (
        time TIMESTAMPTZ NOT NULL DEFAULT now(),
        latency_ms NUMERIC,
        download_mbps NUMERIC,
        upload_mbps NUMERIC,
        download_bytes BIGINT,
        upload_bytes BIGINT,
        raw JSONB
    );

    CREATE TABLE IF NOT EXISTS cell_id_tags (
        cell_id TEXT PRIMARY KEY,
        iso_region_code TEXT NOT NULL,
        custom_label TEXT,
        notes TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    ALTER TABLE cell_id_tags
        ADD COLUMN IF NOT EXISTS iso_region_code TEXT,
        ADD COLUMN IF NOT EXISTS custom_label TEXT,
        ADD COLUMN IF NOT EXISTS notes TEXT,
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'cell_id_tags'
              AND column_name = 'tag'
        ) THEN
            ALTER TABLE cell_id_tags ALTER COLUMN tag DROP NOT NULL;
        END IF;
    END $$;

    SELECT create_hypertable(
        'cellular_metrics',
        'time',
        if_not_exists => TRUE
    );

    SELECT create_hypertable(
        'speedtest_results',
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


def insert_speedtest_result(data):
    if not db_config_available():
        return

    sql = """
    INSERT INTO speedtest_results (
        time,
        latency_ms,
        download_mbps,
        upload_mbps,
        download_bytes,
        upload_bytes,
        raw
    )
    VALUES (
        %(time)s::timestamptz,
        %(latency_ms)s,
        %(download_mbps)s,
        %(upload_mbps)s,
        %(download_bytes)s,
        %(upload_bytes)s,
        %(raw)s
    );
    """

    row = {
        "time": data.get("time"),
        "latency_ms": data.get("latency_ms"),
        "download_mbps": data.get("download_mbps"),
        "upload_mbps": data.get("upload_mbps"),
        "download_bytes": data.get("download_bytes"),
        "upload_bytes": data.get("upload_bytes"),
        "raw": Json(data),
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, row)


def fetch_speedtest_results(minutes=1440, limit=24, start=None, end=None):
    if not db_config_available():
        return []

    minutes = max(1, int(minutes))
    limit = min(max(1, int(limit)), 500)

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
        latency_ms,
        download_mbps,
        upload_mbps,
        download_bytes,
        upload_bytes,
        raw->>'server_location' AS server_location
    FROM speedtest_results
    WHERE {where_clause}
    ORDER BY time DESC
    LIMIT %s;
    """
    params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    keys = [
        "time",
        "latency_ms",
        "download_mbps",
        "upload_mbps",
        "download_bytes",
        "upload_bytes",
        "server_location",
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


def prune_speedtest_results(retention_days):
    if not db_config_available() or not retention_days:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM speedtest_results WHERE time < now() - (%s || ' days')::interval;",
                (int(retention_days),),
            )


def cell_tag_label(row):
    iso_region_code = row.get("iso_region_code")
    custom_label = row.get("custom_label")
    if custom_label and iso_region_code:
        return f"{custom_label} - {iso_region_code}"
    return custom_label or iso_region_code


def upsert_cell_tag(cell_id, iso_region_code, custom_label=None, notes=None):
    if not db_config_available():
        return

    normalized_cell_id = str(cell_id or "").strip()
    normalized_iso_region_code = str(iso_region_code or "").strip()
    normalized_custom_label = str(custom_label or "").strip() or None
    if not normalized_cell_id:
        raise ValueError("cell_id is required")
    if not normalized_iso_region_code:
        raise ValueError("iso_region_code is required")

    sql = """
    INSERT INTO cell_id_tags (
        cell_id,
        iso_region_code,
        custom_label,
        notes,
        updated_at
    )
    VALUES (%s, %s, %s, %s, now())
    ON CONFLICT (cell_id)
    DO UPDATE SET
        iso_region_code = EXCLUDED.iso_region_code,
        custom_label = EXCLUDED.custom_label,
        notes = EXCLUDED.notes,
        updated_at = now();
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (normalized_cell_id, normalized_iso_region_code, normalized_custom_label, notes),
            )


def fetch_cell_tags(cell_ids=None):
    if not db_config_available():
        return []

    params = []
    where_clause = ""
    if cell_ids:
        normalized_cell_ids = [str(value).strip() for value in cell_ids if str(value).strip()]
        if not normalized_cell_ids:
            return []
        where_clause = "WHERE cell_id = ANY(%s)"
        params.append(normalized_cell_ids)

    sql = f"""
    SELECT cell_id, iso_region_code, custom_label, notes, updated_at
    FROM cell_id_tags
    {where_clause}
    ORDER BY iso_region_code, custom_label, cell_id;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    result = []
    for cell_id, iso_region_code, custom_label, notes, updated_at in rows:
        item = {
            "cell_id": cell_id,
            "iso_region_code": iso_region_code,
            "custom_label": custom_label,
            "notes": notes,
            "updated_at": updated_at.isoformat(),
        }
        item["cell_tag"] = cell_tag_label(item)
        result.append(item)

    return result


def fetch_history_cell_groups(minutes=1440, start=None, end=None):
    if not db_config_available():
        return []

    minutes = max(1, int(minutes))
    where_clause = "cm.time >= now() - (%s || ' minutes')::interval"
    params = [minutes]

    if start and end:
        where_clause = "cm.time >= %s::timestamptz AND cm.time <= %s::timestamptz"
        params = [start, end]
    elif start:
        where_clause = "cm.time >= %s::timestamptz"
        params = [start]
    elif end:
        where_clause = "cm.time <= %s::timestamptz"
        params = [end]

    sql = f"""
    SELECT
        cm.cell_id,
        cit.iso_region_code,
        cit.custom_label,
        cit.notes,
        COUNT(*) AS sample_count,
        MIN(cm.time) AS first_seen,
        MAX(cm.time) AS last_seen,
        AVG(cm.rsrp) AS avg_rsrp,
        AVG(cm.rsrq) AS avg_rsrq,
        AVG(cm.sinr) AS avg_sinr,
        AVG(cm.rssi) AS avg_rssi
    FROM cellular_metrics cm
    LEFT JOIN cell_id_tags cit ON cit.cell_id = cm.cell_id
    WHERE {where_clause}
      AND cm.cell_id IS NOT NULL
      AND cm.cell_id <> ''
    GROUP BY cm.cell_id, cit.iso_region_code, cit.custom_label, cit.notes
    ORDER BY last_seen DESC, sample_count DESC;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    keys = [
        "cell_id",
        "iso_region_code",
        "custom_label",
        "notes",
        "sample_count",
        "first_seen",
        "last_seen",
        "avg_rsrp",
        "avg_rsrq",
        "avg_sinr",
        "avg_rssi",
    ]

    result = []
    for row in rows:
        item = dict(zip(keys, row))
        item["cell_tag"] = cell_tag_label(item)
        item["cell_label"] = item["cell_tag"] or item["cell_id"]
        item["first_seen"] = item["first_seen"].isoformat()
        item["last_seen"] = item["last_seen"].isoformat()
        for key, value in list(item.items()):
            if isinstance(value, Decimal):
                item[key] = float(value)
        result.append(item)

    return result


def fetch_history(minutes=1440, limit=500, start=None, end=None, cell_id=None):
    if not db_config_available():
        return []

    minutes = max(1, int(minutes))
    limit = min(max(1, int(limit)), 2000)

    where_clause = "cm.time >= now() - (%s || ' minutes')::interval"
    params = [minutes]

    if start and end:
        where_clause = "cm.time >= %s::timestamptz AND cm.time <= %s::timestamptz"
        params = [start, end]
    elif start:
        where_clause = "cm.time >= %s::timestamptz"
        params = [start]
    elif end:
        where_clause = "cm.time <= %s::timestamptz"
        params = [end]

    if cell_id:
        where_clause = f"{where_clause} AND cm.cell_id = %s"
        params.append(str(cell_id))

    sql = f"""
    SELECT
        cm.time,
        cm.network_type,
        cm.operator,
        cm.pci,
        cm.cell_id,
        cit.iso_region_code,
        cit.custom_label,
        cit.notes,
        cm.enodeb_id,
        cm.rsrp,
        cm.rsrq,
        cm.sinr,
        cm.rssi,
        cm.band,
        cm.earfcn,
        cm.dl_bandwidth,
        cm.ul_bandwidth,
        cm.tac,
        cm.plmn,
        cm.transmode,
        cm.cqi0
    FROM cellular_metrics cm
    LEFT JOIN cell_id_tags cit ON cit.cell_id = cm.cell_id
    WHERE {where_clause}
    ORDER BY cm.time ASC;
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
        "iso_region_code",
        "custom_label",
        "notes",
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
        item["cell_tag"] = cell_tag_label(item)
        item["cell_label"] = item["cell_tag"] or item["cell_id"]
        item["time"] = item["time"].isoformat()
        for key, value in list(item.items()):
            if isinstance(value, Decimal):
                item[key] = float(value)
        result.append(item)

    return result
