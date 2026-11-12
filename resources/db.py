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
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "timescaledb"),
                port=int(os.getenv("DB_PORT", "5432")),
                dbname=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                connect_timeout=5,
            )
            timezone_name = os.getenv("TZ")
            if timezone_name:
                with conn.cursor() as cur:
                    cur.execute("SET TIME ZONE %s;", (timezone_name,))
            return conn
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

        router_vendor TEXT,
        router_name TEXT,

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
        cell_id TEXT NOT NULL,
        pci TEXT NOT NULL DEFAULT '',
        router_vendor TEXT,
        iso_region_code TEXT NOT NULL,
        custom_label TEXT,
        profile_enodeb_id TEXT,
        profile_tac TEXT,
        profile_plmn TEXT,
        profile_transmode TEXT,
        profile_cqi0 TEXT,
        custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
        notes TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    ALTER TABLE cell_id_tags
        ADD COLUMN IF NOT EXISTS pci TEXT DEFAULT '',
        ADD COLUMN IF NOT EXISTS router_vendor TEXT,
        ADD COLUMN IF NOT EXISTS iso_region_code TEXT,
        ADD COLUMN IF NOT EXISTS custom_label TEXT,
        ADD COLUMN IF NOT EXISTS profile_enodeb_id TEXT,
        ADD COLUMN IF NOT EXISTS profile_tac TEXT,
        ADD COLUMN IF NOT EXISTS profile_plmn TEXT,
        ADD COLUMN IF NOT EXISTS profile_transmode TEXT,
        ADD COLUMN IF NOT EXISTS profile_cqi0 TEXT,
        ADD COLUMN IF NOT EXISTS custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS notes TEXT,
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

    ALTER TABLE cellular_metrics
        ADD COLUMN IF NOT EXISTS router_vendor TEXT,
        ADD COLUMN IF NOT EXISTS router_name TEXT;

    UPDATE cell_id_tags SET pci = '' WHERE pci IS NULL;
    ALTER TABLE cell_id_tags ALTER COLUMN pci SET DEFAULT '';
    ALTER TABLE cell_id_tags ALTER COLUMN pci SET NOT NULL;

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

    DO $$
    DECLARE
        constraint_name TEXT;
        constrained_columns TEXT[];
    BEGIN
        SELECT con.conname, array_agg(att.attname ORDER BY ord.ordinality)
        INTO constraint_name, constrained_columns
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS ord(attnum, ordinality) ON TRUE
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ord.attnum
        WHERE con.conrelid = 'cell_id_tags'::regclass
          AND con.contype = 'p'
        GROUP BY con.conname;

        IF constraint_name IS NOT NULL AND constrained_columns = ARRAY['cell_id'] THEN
            EXECUTE format('ALTER TABLE cell_id_tags DROP CONSTRAINT %I', constraint_name);
        END IF;
    END $$;

    CREATE UNIQUE INDEX IF NOT EXISTS cell_id_tags_cell_id_pci_idx
        ON cell_id_tags (cell_id, pci);

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
        router_vendor, router_name,
        network_type, operator,
        pci, cell_id, enodeb_id,
        rsrp, rsrq, sinr, rssi,
        band, earfcn, dl_bandwidth, ul_bandwidth,
        tac, plmn, transmode, cqi0,
        raw
    )
    VALUES (
        %(router_vendor)s, %(router_name)s,
        %(network_type)s, %(operator)s,
        %(pci)s, %(cell_id)s, %(enodeb_id)s,
        %(rsrp)s, %(rsrq)s, %(sinr)s, %(rssi)s,
        %(band)s, %(earfcn)s, %(dl_bandwidth)s, %(ul_bandwidth)s,
        %(tac)s, %(plmn)s, %(transmode)s, %(cqi0)s,
        %(raw)s
    );
    """

    row = {
        "router_vendor": data.get("router_vendor"),
        "router_name": data.get("router_name"),
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


def upsert_cell_tag(
    cell_id,
    pci,
    iso_region_code,
    custom_label=None,
    notes=None,
    router_vendor=None,
    profile_enodeb_id=None,
    profile_tac=None,
    profile_plmn=None,
    profile_transmode=None,
    profile_cqi0=None,
    custom_fields=None,
):
    if not db_config_available():
        return

    normalized_cell_id = str(cell_id or "").strip()
    normalized_pci = str(pci or "").strip()
    normalized_iso_region_code = str(iso_region_code or "").strip()
    normalized_custom_label = str(custom_label or "").strip() or None
    normalized_router_vendor = str(router_vendor or "").strip().lower() or None
    normalized_custom_fields = custom_fields if isinstance(custom_fields, dict) else {}
    if not normalized_cell_id:
        raise ValueError("cell_id is required")
    if not normalized_iso_region_code:
        raise ValueError("iso_region_code is required")

    sql = """
    INSERT INTO cell_id_tags (
        cell_id,
        pci,
        router_vendor,
        iso_region_code,
        custom_label,
        profile_enodeb_id,
        profile_tac,
        profile_plmn,
        profile_transmode,
        profile_cqi0,
        custom_fields,
        notes,
        updated_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (cell_id, pci)
    DO UPDATE SET
        router_vendor = EXCLUDED.router_vendor,
        iso_region_code = EXCLUDED.iso_region_code,
        custom_label = EXCLUDED.custom_label,
        profile_enodeb_id = EXCLUDED.profile_enodeb_id,
        profile_tac = EXCLUDED.profile_tac,
        profile_plmn = EXCLUDED.profile_plmn,
        profile_transmode = EXCLUDED.profile_transmode,
        profile_cqi0 = EXCLUDED.profile_cqi0,
        custom_fields = EXCLUDED.custom_fields,
        notes = EXCLUDED.notes,
        updated_at = now();
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    normalized_cell_id,
                    normalized_pci,
                    normalized_router_vendor,
                    normalized_iso_region_code,
                    normalized_custom_label,
                    profile_enodeb_id,
                    profile_tac,
                    profile_plmn,
                    profile_transmode,
                    profile_cqi0,
                    Json(normalized_custom_fields),
                    notes,
                ),
            )


def fetch_cell_tags(cell_ids=None, pci=None, router_vendor=None):
    if not db_config_available():
        return []

    params = []
    where_clause = ""
    filters = []
    if cell_ids:
        normalized_cell_ids = [str(value).strip() for value in cell_ids if str(value).strip()]
        if not normalized_cell_ids:
            return []
        filters.append("cell_id = ANY(%s)")
        params.append(normalized_cell_ids)
    if pci is not None:
        filters.append("pci = %s")
        params.append(str(pci or "").strip())
    if router_vendor:
        filters.append("router_vendor = %s")
        params.append(str(router_vendor).strip().lower())
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    sql = f"""
    SELECT
        cell_id,
        pci,
        router_vendor,
        iso_region_code,
        custom_label,
        profile_enodeb_id,
        profile_tac,
        profile_plmn,
        profile_transmode,
        profile_cqi0,
        custom_fields,
        notes,
        updated_at
    FROM cell_id_tags
    {where_clause}
    ORDER BY iso_region_code, custom_label, cell_id, pci;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    result = []
    for (
        cell_id,
        pci,
        router_vendor,
        iso_region_code,
        custom_label,
        profile_enodeb_id,
        profile_tac,
        profile_plmn,
        profile_transmode,
        profile_cqi0,
        custom_fields,
        notes,
        updated_at,
    ) in rows:
        item = {
            "cell_id": cell_id,
            "pci": pci,
            "router_vendor": router_vendor,
            "iso_region_code": iso_region_code,
            "custom_label": custom_label,
            "profile_enodeb_id": profile_enodeb_id,
            "profile_tac": profile_tac,
            "profile_plmn": profile_plmn,
            "profile_transmode": profile_transmode,
            "profile_cqi0": profile_cqi0,
            "custom_fields": custom_fields or {},
            "notes": notes,
            "updated_at": updated_at.isoformat(),
        }
        item["cell_tag"] = cell_tag_label(item)
        result.append(item)

    return result


def fetch_history_cell_groups(minutes=1440, start=None, end=None, router_vendor=None):
    if not db_config_available():
        return []

    minutes = max(1, int(minutes))
    where_clause = "time >= now() - (%s || ' minutes')::interval"
    filtered_params = [minutes]

    if start and end:
        where_clause = "time >= %s::timestamptz AND time <= %s::timestamptz"
        filtered_params = [start, end]
    elif start:
        where_clause = "time >= %s::timestamptz"
        filtered_params = [start]
    elif end:
        where_clause = "time <= %s::timestamptz"
        filtered_params = [end]

    all_seen_filters = [
        "cell_id IS NOT NULL",
        "cell_id <> ''",
    ]
    if router_vendor:
        normalized_router_vendor = str(router_vendor).strip().lower()
        where_clause = f"{where_clause} AND router_vendor = %s"
        filtered_params.append(normalized_router_vendor)
        all_seen_filters.append("router_vendor = %s")
        all_seen_params = [normalized_router_vendor]
    else:
        all_seen_params = []

    sql = f"""
    WITH all_seen AS (
        SELECT
            cell_id,
            COALESCE(pci, '') AS pci,
            COALESCE(enodeb_id, '') AS enodeb_id,
            COALESCE(tac, '') AS tac,
            router_vendor,
            MIN(time) AS first_seen
        FROM cellular_metrics
        WHERE {" AND ".join(all_seen_filters)}
        GROUP BY cell_id, COALESCE(pci, ''), COALESCE(enodeb_id, ''), COALESCE(tac, ''), router_vendor
    ),
    filtered_metrics AS (
        SELECT *
        FROM cellular_metrics
        WHERE {where_clause}
          AND cell_id IS NOT NULL
          AND cell_id <> ''
    )
    SELECT
        fm.cell_id,
        COALESCE(fm.pci, '') AS pci,
        COALESCE(fm.enodeb_id, '') AS enodeb_id,
        COALESCE(fm.tac, '') AS tac,
        MAX(cit.profile_enodeb_id) AS profile_enodeb_id,
        MAX(cit.profile_tac) AS profile_tac,
        cit.router_vendor AS tag_router_vendor,
        cit.iso_region_code,
        cit.custom_label,
        cit.notes,
        COUNT(*) AS sample_count,
        MIN(all_seen.first_seen) AS first_seen,
        MAX(fm.time) AS last_seen,
        AVG(fm.rsrp) AS avg_rsrp,
        AVG(fm.rsrq) AS avg_rsrq,
        AVG(fm.sinr) AS avg_sinr,
        AVG(fm.rssi) AS avg_rssi
    FROM filtered_metrics fm
    LEFT JOIN all_seen
      ON all_seen.cell_id = fm.cell_id
     AND all_seen.pci = COALESCE(fm.pci, '')
     AND all_seen.enodeb_id = COALESCE(fm.enodeb_id, '')
     AND all_seen.tac = COALESCE(fm.tac, '')
     AND all_seen.router_vendor IS NOT DISTINCT FROM fm.router_vendor
    LEFT JOIN cell_id_tags cit ON cit.cell_id = fm.cell_id AND cit.pci = COALESCE(fm.pci, '')
      AND (cit.router_vendor = fm.router_vendor OR cit.router_vendor IS NULL OR cit.router_vendor = '')
    GROUP BY fm.cell_id, COALESCE(fm.pci, ''), COALESCE(fm.enodeb_id, ''), COALESCE(fm.tac, ''), cit.router_vendor, cit.iso_region_code, cit.custom_label, cit.notes
    ORDER BY last_seen DESC, sample_count DESC;
    """
    params = all_seen_params + filtered_params

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    keys = [
        "cell_id",
        "pci",
        "enodeb_id",
        "tac",
        "profile_enodeb_id",
        "profile_tac",
        "tag_router_vendor",
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


def fetch_history(minutes=1440, limit=500, start=None, end=None, cell_id=None, router_vendor=None, latest=False,
                  pci=None, enodeb_id=None, tac=None):
    if not db_config_available():
        return []

    minutes = max(1, int(minutes))
    limit = min(max(1, int(limit)), 2000)

    where_clause = "TRUE" if latest else "cm.time >= now() - (%s || ' minutes')::interval"
    params = [] if latest else [minutes]

    if not latest and start and end:
        where_clause = "cm.time >= %s::timestamptz AND cm.time <= %s::timestamptz"
        params = [start, end]
    elif not latest and start:
        where_clause = "cm.time >= %s::timestamptz"
        params = [start]
    elif not latest and end:
        where_clause = "cm.time <= %s::timestamptz"
        params = [end]

    if router_vendor:
        where_clause = f"{where_clause} AND cm.router_vendor = %s"
        params.append(str(router_vendor).strip().lower())

    if cell_id:
        where_clause = f"{where_clause} AND cm.cell_id = %s"
        params.append(str(cell_id))

    # None means all sectors; an explicit empty string matches missing metadata.
    for column, value in (("pci", pci), ("enodeb_id", enodeb_id), ("tac", tac)):
        if value is not None:
            where_clause += f" AND COALESCE(cm.{column}, '') = %s"
            params.append(str(value).strip())

    row_limit = "LIMIT 1" if limit == 1 else ""
    sql = f"""
    SELECT
        cm.time,
        cm.router_vendor,
        cm.router_name,
        cm.network_type,
        cm.operator,
        cm.pci,
        cm.cell_id,
        cit.router_vendor AS tag_router_vendor,
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
    LEFT JOIN cell_id_tags cit ON cit.cell_id = cm.cell_id AND cit.pci = COALESCE(cm.pci, '')
      AND (cit.router_vendor = cm.router_vendor OR cit.router_vendor IS NULL OR cit.router_vendor = '')
    WHERE {where_clause}
    ORDER BY cm.time DESC
    {row_limit};
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    if len(rows) > limit and limit == 1:
        rows = [rows[0]]
    elif len(rows) > limit:
        step = (len(rows) - 1) / (limit - 1)
        rows = [rows[round(index * step)] for index in range(limit)]

    rows = list(reversed(rows))

    keys = [
        "time",
        "router_vendor",
        "router_name",
        "network_type",
        "operator",
        "pci",
        "cell_id",
        "tag_router_vendor",
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
