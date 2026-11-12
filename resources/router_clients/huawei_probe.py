"""Manual diagnostics only. All capability requests use GET without redirects."""

import re
import xml.etree.ElementTree as ET

import requests


SIGNAL = "/api/device/signal"
ENDPOINTS = ("/api/device/nbrcellinfo", "/api/ntwk/celllock", "/api/net/lock-cell",
             "/api/net/lock-freq")
TOKEN_ERRORS = {"125001", "125002", "125003"}


def parse_neighbor_cells(fields):
    """Observed firmware's LTE rows; order verified against its cell UI JS.

    Other XML leaves remain available in fields. Do not guess schemas for
    unknown lists or different row widths.
    """
    cells, unparsed = [], []
    columns = ("earfcn", "band", "pci", "rsrp", "rsrq", "rssi", "sinr")
    for field in fields:
        name = re.sub(r"[^a-z0-9]", "", field["path"].split("/")[-1].split("[")[0].lower())
        if name != "nbrcellltelist":
            continue
        for row in field["value"].split(";"):
            if not row.strip():
                continue
            values = [value.strip() for value in row.split(",")]
            if len(values) != len(columns):
                unparsed.append(row)
                continue
            cells.append({key: None if value in ("", "--") else value
                          for key, value in zip(columns, values)})
    return {"lte": cells, "unparsed_rows": unparsed}


def probe_cell_lock_capabilities(client):
    """Reuse the client's session/login; return only sanitized response bodies.

    A new CLI client logs in once if needed. An already authenticated client
    keeps its session. Never relogin just because a capability is unsupported.
    """
    tokens = [value for key, value in client.headers.items()
              if key.lower() == "__requestverificationtoken" and value]
    secret_values = [client.username, client.password]

    def capture(response, *args, **kwargs):
        for key, value in response.headers.items():
            if "token" in key.lower():
                secret_values.append(value)
                if "requestverificationtoken" in key.lower():
                    tokens.extend(part for part in value.split("#") if part)
            elif key.lower() == "set-cookie":
                secret_values.append(value)
        secret_values.extend(client.session.cookies.values())

    def sanitize(body):
        # Keep the original structure/whitespace unless sensitive material occurs.
        for value in sorted(set(filter(None, secret_values + tokens)), key=len, reverse=True):
            body = body.replace(value, "[REDACTED]")
        sensitive = r"[\w:.-]*(?:token|cookie|session|password|passwd|username|nonce|salt|proof|authorization)[\w:.-]*"
        body = re.sub(r"(?is)(<(" + sensitive + r")\b[^>]*>).*?(</\2\s*>)",
                      r"\1[REDACTED]\3", body)
        body = re.sub(r'(?i)([\"\']?' + sensitive + r'[\"\']?\s*[:=]\s*)([\"\'])(.*?)\2',
                      r'\1\2[REDACTED]\2', body)
        body = re.sub(r'(?i)((?:SessionID|__RequestVerificationToken)\s*=\s*)[^\s;<\"\']+',
                      r'\1[REDACTED]', body)
        body = re.sub(r'(?is)<(?:meta|input)\b[^>]*(?:token|password|session|cookie)[^>]*>',
                      '<!-- sensitive element redacted -->', body)
        return body

    def read(endpoint, token=None):
        headers = {key: value for key, value in client.headers.items()
                   if key.lower() != "__requestverificationtoken"}
        if token:
            headers["__RequestVerificationToken"] = token
        result = {"endpoint": endpoint, "http_status": None, "xml_parsed": False,
                  "api_error_code": None, "result": "unknown", "token_sent": bool(token),
                  "body": "", "fields": []}
        try:
            response = client.session.get(client.router_url + endpoint, headers=headers,
                                          timeout=10, allow_redirects=False)
        except requests.RequestException as exc:
            # Exception messages can include URLs/headers; expose only the type.
            result["transport_error"] = type(exc).__name__
            return result
        result["http_status"] = response.status_code
        result["body"] = sanitize(response.text)
        try:
            root = ET.fromstring(result["body"])
            result["xml_parsed"] = True
            tag = lambda element: element.tag.rsplit("}", 1)[-1].lower()
            errors = [element for element in root.iter() if tag(element) == "error"]
            if errors:
                result["api_error_code"] = next(
                    ((element.text or "").strip() for element in errors[0].iter()
                     if tag(element) == "code"), None)
            def walk(element, path):
                if not len(element):
                    result["fields"].append({"path": path, "value": element.text or ""})
                for index, child in enumerate(element):
                    walk(child, f"{path}/{child.tag}[{index}]")
            walk(root, root.tag)
            if 200 <= response.status_code < 300 and tag(root) == "response" and not errors:
                result["result"] = "supported"
        except ET.ParseError:
            pass
        code = result["api_error_code"]
        if code in TOKEN_ERRORS or response.status_code == 401:
            result["result"] = "authentication/token issue"
        elif code == "100003" or response.status_code == 403:
            result["result"] = "permission denied"
        elif code == "100002" or response.status_code in (404, 410):
            result["result"] = "unsupported"
        return result

    def read_with_retry(endpoint):
        first = read(endpoint)
        if endpoint == "/api/net/lock-freq":
            # This probe only retries a token/auth failure with an already
            # available token. Do not fetch a page/token or relogin here.
            if first["api_error_code"] not in TOKEN_ERRORS and first["http_status"] != 401:
                return first
            if not tokens:
                first["token_retry"] = "existing token unavailable"
                return first
            retry = read(endpoint, tokens[-1])
            retry["attempts"] = [first]
            return retry
        # Some firmware reports no-rights for a missing verification header.
        if first["api_error_code"] not in TOKEN_ERRORS | {"100003"}:
            return first
        if not tokens:
            try:
                tokens.extend(client._get_page_tokens())
                secret_values.extend(tokens)
            except Exception:
                first["token_retry"] = "token unavailable"
                return first
        retry = read(endpoint, tokens[-1])
        retry["attempts"] = [first]
        return retry

    hooks = client.session.hooks.setdefault("response", [])
    hooks.append(capture)
    try:
        before = read_with_retry(SIGNAL)
        if before["result"] in ("permission denied", "authentication/token issue"):
            initial = before
            try:
                client.login()
            except Exception as exc:
                return {"before": initial, "probes": [], "after": None,
                        "aborted": "Authentication failed (" + type(exc).__name__ + ")",
                        "configuration_writes": 0}
            before = read_with_retry(SIGNAL)
            before["session_validation_before_login"] = initial
        report = {"before": before, "probes": [], "after": None,
                  "configuration_writes": 0}
        if before["result"] != "supported" or not any(
                re.search(r"pci|cell.?id|rsrp|rssi|sinr", field["path"], re.I)
                for field in before["fields"]):
            report["aborted"] = "Signal validation failed; capability probes skipped"
            return report
        report["probes"] = [read_with_retry(endpoint) for endpoint in ENDPOINTS]
        neighbor = report["probes"][0]
        if neighbor["result"] == "supported":
            neighbor["neighbor_cells"] = parse_neighbor_cells(neighbor["fields"])
        report["after"] = read_with_retry(SIGNAL)
        def serving(result):
            return {field["path"]: field["value"] for field in result["fields"]
                    if re.search(r"pci|cell.?id|enodeb|earfcn|arfcn|band", field["path"], re.I)}
        old, new = serving(before), serving(report["after"])
        report["serving_before"] = old
        report["serving_after"] = new
        report["serving_cell_unchanged"] = old == new if old and new else None
        report["connectivity_after"] = report["after"]["result"] == "supported"
        return report
    finally:
        hooks.remove(capture)
