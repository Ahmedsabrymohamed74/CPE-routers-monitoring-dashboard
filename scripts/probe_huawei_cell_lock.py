#!/usr/bin/env python3
"""GET-only Huawei diagnostics using the app's environment variable names."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "resources"))
from router_clients.huawei import HuaweiRouterClient


def env(name, default=None, alias=None):
    return os.environ.get(name) or (os.environ.get(alias) if alias else None) or default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print full sanitized report")
    parser.add_argument("--output", type=Path, help="Save full sanitized report (exclusive create)")
    args = parser.parse_args()
    password = env("HUAWEI_ROUTER_PASSWORD", alias="ROUTER_PASSWORD")
    if not password:
        parser.error("HUAWEI_ROUTER_PASSWORD (or ROUTER_PASSWORD) must be configured")
    client = HuaweiRouterClient(
        env("HUAWEI_ROUTER_URL", "http://192.168.9.1", "ROUTER_URL"),
        env("HUAWEI_ROUTER_USERNAME", "admin", "ROUTER_USERNAME"), password)
    report = client.probe_cell_lock_capabilities()
    serialized = json.dumps(report, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(serialized + "\n")
    if args.json:
        print(serialized)
    else:
        for result in [report["before"], *report["probes"], report["after"]]:
            if result is None:
                continue
            print(f'{result["endpoint"]}\n  status: {result["http_status"]}\n'
                  f'  result: {result["result"]}\n  XML parsed: {result["xml_parsed"]}\n'
                  f'  API error: {result["api_error_code"]}\n  token sent: {result["token_sent"]}')
            if "neighbor_cells" in result:
                for cell in result["neighbor_cells"]["lte"]:
                    print("  LTE neighbor: " + json.dumps(cell))
            elif result["fields"] and result["endpoint"] != "/api/device/signal":
                print("  parsed fields: " + json.dumps(result["fields"]))
            if result["endpoint"] != "/api/device/signal":
                print("  body: " + result["body"])
        for key in ("aborted", "serving_before", "serving_after", "connectivity_after",
                    "serving_cell_unchanged", "configuration_writes"):
            if key in report:
                print(f"{key}: {report[key]}")
    return 1 if report.get("aborted") or not report.get("connectivity_after") else 0


if __name__ == "__main__":
    sys.exit(main())
