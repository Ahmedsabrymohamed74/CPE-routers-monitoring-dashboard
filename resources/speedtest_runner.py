import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


CLOUDFLARE_COLO_LOCATIONS = {
    "AMS": "Amsterdam, Netherlands",
    "ATH": "Athens, Greece",
    "BEG": "Belgrade, Serbia",
    "CAI": "Cairo, Egypt",
    "CDG": "Paris, France",
    "CPH": "Copenhagen, Denmark",
    "DUB": "Dublin, Ireland",
    "DUS": "Dusseldorf, Germany",
    "FCO": "Rome, Italy",
    "FRA": "Frankfurt, Germany",
    "IST": "Istanbul, Turkey",
    "LHR": "London, United Kingdom",
    "MAD": "Madrid, Spain",
    "MAN": "Manchester, United Kingdom",
    "MRS": "Marseille, France",
    "MXP": "Milan, Italy",
    "PMO": "Palermo, Italy",
    "PRG": "Prague, Czechia",
    "VIE": "Vienna, Austria",
    "WAW": "Warsaw, Poland",
    "ZRH": "Zurich, Switzerland",
}


def local_now_iso():
    return datetime.now().astimezone().isoformat()


def parse_cloudflare_trace_location(text):
    for line in text.splitlines():
        if line.startswith("colo="):
            colo = line.split("=", 1)[1].strip().upper()
            location = CLOUDFLARE_COLO_LOCATIONS.get(colo)
            return location or f"Cloudflare edge {colo}"
    return None


def measure_latency_ms(url, attempts, timeout_seconds):
    samples = []
    server_location = None

    session = requests.Session()
    response = session.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    server_location = parse_cloudflare_trace_location(response.text)

    for _ in range(max(1, attempts)):
        started = time.perf_counter()
        response = session.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        samples.append((time.perf_counter() - started) * 1000)
        if server_location is None:
            server_location = parse_cloudflare_trace_location(response.text)

    return sum(samples) / len(samples), server_location


def download_bytes(url_template, byte_count, timeout_seconds):
    url = url_template.format(bytes=byte_count)
    received = 0

    with requests.get(url, stream=True, timeout=timeout_seconds) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if chunk:
                received += len(chunk)

    return received


def measure_download_mbps(url_template, byte_count, timeout_seconds, parallel_streams):
    started = time.perf_counter()
    total_received = 0

    with ThreadPoolExecutor(max_workers=parallel_streams) as executor:
        futures = [
            executor.submit(download_bytes, url_template, byte_count, timeout_seconds)
            for _ in range(parallel_streams)
        ]
        for future in as_completed(futures):
            total_received += future.result()

    elapsed = max(time.perf_counter() - started, 0.001)
    return (total_received * 8) / elapsed / 1_000_000, total_received


def upload_bytes(url, byte_count, timeout_seconds):
    payload = b"0" * byte_count
    response = requests.post(url, data=payload, timeout=timeout_seconds)
    response.raise_for_status()
    return byte_count


def measure_upload_mbps(url, byte_count, timeout_seconds, parallel_streams):
    started = time.perf_counter()
    total_sent = 0

    with ThreadPoolExecutor(max_workers=parallel_streams) as executor:
        futures = [
            executor.submit(upload_bytes, url, byte_count, timeout_seconds)
            for _ in range(parallel_streams)
        ]
        for future in as_completed(futures):
            total_sent += future.result()

    elapsed = max(time.perf_counter() - started, 0.001)
    return (total_sent * 8) / elapsed / 1_000_000, total_sent


def run_speedtest(config):
    started_at = local_now_iso()
    latency_ms, server_location = measure_latency_ms(
        config["latency_url"],
        config["latency_attempts"],
        config["timeout_seconds"],
    )
    parallel_streams = max(1, config["parallel_streams"])
    download_mbps, downloaded_bytes = measure_download_mbps(
        config["download_url"],
        config["download_bytes"],
        config["timeout_seconds"],
        parallel_streams,
    )
    upload_mbps, uploaded_bytes = measure_upload_mbps(
        config["upload_url"],
        config["upload_bytes"],
        config["timeout_seconds"],
        parallel_streams,
    )

    return {
        "time": started_at,
        "latency_ms": round(latency_ms, 2),
        "download_mbps": round(download_mbps, 2),
        "upload_mbps": round(upload_mbps, 2),
        "download_bytes": downloaded_bytes,
        "upload_bytes": uploaded_bytes,
        "parallel_streams": parallel_streams,
        "server_location": server_location,
    }
