import base64
import hashlib
import time

import requests
import urllib3

from .base import BaseRouterClient

urllib3.disable_warnings()


class ZteRouterClient(BaseRouterClient):
    vendor = "zte"

    SIGNAL_COMMANDS = (
        "network_type",
        "rssi",
        "rsrq",
        "Z_rsrq",
        "rscp",
        "lte_rsrp",
        "lte_rsrq",
        "Z5g_snr",
        "Z5g_rsrp",
        "ZCELLINFO_band",
        "Z5g_dlEarfcn",
        "lte_ca_pcell_arfcn",
        "lte_ca_pcell_band",
        "lte_ca_scell_band",
        "lte_ca_pcell_bandwidth",
        "lte_ca_scell_info",
        "lte_ca_scell_bandwidth",
        "wan_lte_ca",
        "Z_PCI",
        "Z5g_CELL_ID",
        "Z5g_SINR",
        "cell_id",
        "enodeb_id",
        "eNBID",
        "lte_tac",
        "tac",
        "plmn",
        "mcc_mnc",
        "lte_mcc",
        "lte_mnc",
        "transmode",
        "trans_mode",
        "lte_transmode",
        "cqi",
        "cqi0",
        "lte_cqi",
        "lte_ca_scell_arfcn",
        "lte_multi_ca_scell_info",
        "Z5g_PCI",
        "Z5g_CELLINFO_band",
        "sinr",
        "ecio",
        "Z_dl_earfcn",
        "wan_active_band",
    )

    def __init__(
        self,
        router_url,
        username=None,
        password=None,
        session_cookie=None,
        auth_mode="zte_mf296c",
        auto_max_attempts=3,
        session_ttl_seconds=300,
        router_name=None,
        **kwargs,
    ):
        super().__init__(router_url=router_url, router_name=router_name)
        self.username = username
        self.password = password
        self.session_cookie_name = self._session_cookie_name(session_cookie)
        self.session_cookie = self._normalize_session_cookie(session_cookie)
        self.auth_mode = (auth_mode or "auto").strip().lower()
        self.auto_max_attempts = max(1, int(auto_max_attempts or 1))
        self.session_ttl_seconds = max(60, int(session_ttl_seconds or 300))
        self.authenticated_at = None
        self.session = requests.Session()
        self.session.verify = False

        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Connection": "keep-alive",
            "DNT": "1",
            "Referer": f"{self.router_url}/index.html",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
            "X-Requested-With": "XMLHttpRequest",
            "sec-gpc": "1",
        }

    @staticmethod
    def _session_cookie_name(session_cookie):
        if not session_cookie:
            return None

        value = session_cookie.strip().lower()
        if value.startswith("stok="):
            return "stok"
        return "zsidn"

    @staticmethod
    def _normalize_session_cookie(session_cookie):
        if not session_cookie:
            return None

        value = session_cookie.strip()
        if value.lower() in {"replace-me", "changeme", "none", "null"}:
            return None

        if value.startswith("zsidn="):
            value = value.split("=", 1)[1]
        if value.startswith("stok="):
            value = value.split("=", 1)[1]
        return value.strip().strip('"')

    def _require_session_cookie(self):
        if self.session_cookie and not self._session_expired():
            return

        if self.session_cookie:
            print("[*] ZTE session TTL expired; renewing login", flush=True)
            self._reset_auth()

        self.login()

        if not self.session_cookie:
            raise RuntimeError(
                "ZTE authentication did not return a zsidn session cookie"
            )

    def _get_cmd(self, *commands):
        response = self.session.get(
            f"{self.router_url}/goform/goform_get_cmd_process",
            headers=self.headers,
            params={
                "isTest": "false",
                "cmd": ",".join(commands),
                "multi_data": "1",
                "_": str(int(time.time() * 1000)),
            },
            timeout=10,
        )
        response.raise_for_status()
        return self._json_response(response, "ZTE command response")

    def _auth_context(self):
        context = {}

        try:
            context.update(self._get_cmd("Language", "cr_version", "wa_inner_version"))
        except Exception as exc:
            print(f"[!] ZTE version challenge fetch failed: {exc}", flush=True)

        try:
            context.update(self._get_cmd("LD"))
        except Exception as exc:
            print(f"[!] ZTE LD challenge fetch failed: {exc}", flush=True)

        try:
            context.update(self._get_cmd("RD"))
        except Exception as exc:
            print(f"[!] ZTE RD challenge fetch failed: {exc}", flush=True)

        return context

    @staticmethod
    def _json_response(response, label):
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} was not JSON: {response.text[:200]}") from exc

        if not isinstance(payload, dict):
            raise RuntimeError(f"{label} had unexpected shape: {payload!r}")

        return payload

    @staticmethod
    def _sha256_hex(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _md5_hex(value):
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _base64_text(value):
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    def _password_candidates(self, challenge):
        if not self.password:
            raise RuntimeError("ZTE_ROUTER_PASSWORD is required for ZTE router login")

        ld = self.first_value(
            challenge.get("LD"),
            challenge.get("ld"),
            challenge.get("RD"),
            challenge.get("rd"),
        ) or ""

        password_sha = self._sha256_hex(self.password)

        transforms = {
            "zte_mf296c": self._sha256_hex(password_sha.upper() + ld).upper(),
            "plain": self.password,
            "base64": self._base64_text(self.password),
            "sha256": password_sha,
            "sha256_upper": password_sha.upper(),
        }

        if ld:
            transforms.update({
                "sha256_ld": self._sha256_hex(self.password + ld),
                "sha256_ld_upper": self._sha256_hex(self.password + ld).upper(),
                "double_sha256_ld": self._sha256_hex(password_sha + ld),
                "double_sha256_ld_upper": self._sha256_hex(password_sha.upper() + ld).upper(),
                "double_sha256_ld_upper_lower": self._sha256_hex(password_sha.upper() + ld.upper()),
            })

        if self.auth_mode != "auto":
            if self.auth_mode not in transforms:
                available = ", ".join(sorted(transforms))
                raise RuntimeError(f"Unsupported ZTE_AUTH_MODE={self.auth_mode!r}; available: {available}")
            return [(self.auth_mode, transforms[self.auth_mode])]

        order = (
            "zte_mf296c",
            "double_sha256_ld_upper",
            "double_sha256_ld_upper_lower",
            "sha256_ld",
            "double_sha256_ld",
            "sha256_ld_upper",
            "sha256",
            "sha256_upper",
            "base64",
            "plain",
        )
        return [
            (name, transforms[name])
            for name in order
            if name in transforms
        ][: self.auto_max_attempts]

    def _ad_value(self, challenge):
        rd = self.first_value(challenge.get("RD"), challenge.get("rd"))
        cr_version = self.clean_value(challenge.get("cr_version")) or ""
        wa_inner_version = self.clean_value(challenge.get("wa_inner_version")) or ""

        if not rd:
            return None

        prefix = self._md5_hex(cr_version + wa_inner_version)
        return self._md5_hex(prefix + rd).upper()

    def _login_payloads(self, encoded_password, challenge):
        login_payload = {
            "isTest": "false",
            "goformId": "LOGIN",
            "password": encoded_password,
        }

        if self.auth_mode in {"zte_mf296c", "login"}:
            return [login_payload]

        payloads = [login_payload]

        ad_value = self._ad_value(challenge)
        multi_user_payload = {
            "isTest": "false",
            "goformId": "LOGIN_MULTI_USER",
            "user": self.username or "admin",
            "password": encoded_password,
        }
        if ad_value:
            multi_user_payload["AD"] = ad_value

        payloads.insert(0, multi_user_payload)

        if self.username:
            payloads.append({
                **login_payload,
                "username": self.username,
            })
        return payloads

    def _extract_session_cookie(self, response):
        for cookie_name in ("zsidn", "stok"):
            cookie = response.cookies.get(cookie_name)
            if cookie:
                self.session_cookie_name = cookie_name
                self.session_cookie = self._normalize_session_cookie(cookie)
                return

        for cookie in self.session.cookies:
            if cookie.name in {"zsidn", "stok"}:
                self.session_cookie_name = cookie.name
                self.session_cookie = self._normalize_session_cookie(cookie.value)
                return

    @staticmethod
    def _login_succeeded(payload):
        result = str(payload.get("result", "")).lower()
        return result in {"0", "ok", "success"} or payload.get("success") is True

    @classmethod
    def _has_signal_payload(cls, payload):
        signal_fields = (
            "network_type",
            "lte_rsrp",
            "Z5g_rsrp",
            "rsrq",
            "Z_rsrq",
            "lte_rsrq",
            "sinr",
            "Z5g_SINR",
            "Z5g_snr",
            "rssi",
            "Z_PCI",
            "Z5g_PCI",
            "cell_id",
            "Z5g_CELL_ID",
            "ZCELLINFO_band",
            "Z5g_CELLINFO_band",
        )
        return any(cls.clean_value(payload.get(field)) is not None for field in signal_fields)

    @classmethod
    def _payload_summary(cls, payload):
        return {
            "keys": sorted(payload.keys()),
            "result": payload.get("result"),
            "error": payload.get("error"),
            "network_type": payload.get("network_type"),
            "has_signal": cls._has_signal_payload(payload),
        }

    def _reset_auth(self):
        self.session_cookie = None
        self.session_cookie_name = None
        self.authenticated_at = None
        self.session.cookies.clear()

    def _session_expired(self):
        if self.authenticated_at is None:
            return False
        return time.monotonic() - self.authenticated_at >= self.session_ttl_seconds

    @staticmethod
    def _derive_lte_enodeb_id(cell_id):
        try:
            value = int(str(cell_id).strip())
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return str(value // 256)

    def _plmn(self, signal):
        plmn = self.first_value(signal.get("plmn"), signal.get("mcc_mnc"))
        if plmn:
            return plmn

        mcc = self.clean_value(signal.get("lte_mcc"))
        mnc = self.clean_value(signal.get("lte_mnc"))
        if mcc and mnc:
            return f"{mcc}{mnc}"
        return None

    def login(self):
        challenge = self._auth_context()
        errors = []

        for mode, encoded_password in self._password_candidates(challenge):
            for form_data in self._login_payloads(encoded_password, challenge):
                response = self.session.post(
                    f"{self.router_url}/goform/goform_set_cmd_process",
                    headers={
                        **self.headers,
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Origin": self.router_url,
                    },
                    data=form_data,
                    timeout=10,
                )
                response.raise_for_status()
                payload = self._json_response(response, "ZTE login response")
                self._extract_session_cookie(response)

                if self._login_succeeded(payload):
                    self.authenticated_at = time.monotonic()
                    print(f"[*] ZTE login succeeded using auth mode {mode}", flush=True)
                    return True

                errors.append(f"{mode}/{form_data.get('goformId')}: {payload}")

        raise RuntimeError("ZTE login failed: " + "; ".join(errors))

    def get_signal(self, retried_auth=False):
        self._require_session_cookie()

        response = self.session.get(
            f"{self.router_url}/goform/goform_get_cmd_process",
            headers={
                **self.headers,
                "Cookie": f'{self.session_cookie_name or "zsidn"}="{self.session_cookie}"',
            },
            params={
                "isTest": "false",
                "cmd": ",".join(self.SIGNAL_COMMANDS),
                "multi_data": "1",
                "_": str(int(time.time() * 1000)),
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = self._json_response(response, "ZTE router response")

        if payload.get("result") == "failure" or payload.get("error"):
            if retried_auth:
                raise RuntimeError(f"ZTE router request failed after login: {payload}")
            print(f"[!] ZTE signal request returned error payload: {self._payload_summary(payload)}", flush=True)
            self._reset_auth()
            self.login()
            return self.get_signal(retried_auth=True)

        if str(payload.get("result", "")).lower() in {"login", "nologin", "not_login"}:
            if retried_auth:
                raise RuntimeError(f"ZTE router still requires login after authentication: {payload}")
            print(f"[!] ZTE signal request requires login: {self._payload_summary(payload)}", flush=True)
            self._reset_auth()
            self.login()
            return self.get_signal(retried_auth=True)

        if not self._has_signal_payload(payload):
            if retried_auth:
                raise RuntimeError(f"ZTE router returned no signal data after login: {payload}")
            print(f"[!] ZTE signal response had no signal fields: {self._payload_summary(payload)}", flush=True)
            self._reset_auth()
            self.login()
            return self.get_signal(retried_auth=True)

        return payload

    def build_payload(self, operator_name=None, network_type=None, retried_auth=False):
        signal = self.get_signal()
        payload = self.normalize_signal(
            signal,
            operator_name=operator_name,
            network_type=network_type,
        )

        if self.has_cellular_data(payload):
            return payload

        print(
            f"[!] ZTE normalized payload had no cellular data: {self._payload_summary(signal)}",
            flush=True,
        )

        if retried_auth:
            raise RuntimeError(f"No cellular signal data returned by {self.vendor} router")

        self._reset_auth()
        self.login()
        return self.build_payload(
            operator_name=operator_name,
            network_type=network_type,
            retried_auth=True,
        )

    def normalize_signal(self, signal, operator_name=None, network_type=None):
        pcell_band = self.clean_value(signal.get("lte_ca_pcell_band"))
        scell_band = self.clean_value(signal.get("lte_ca_scell_band"))
        pcell_bw = self.clean_value(signal.get("lte_ca_pcell_bandwidth"))
        scell_bw = self.clean_value(signal.get("lte_ca_scell_bandwidth"))

        band_info = None
        if pcell_band or scell_band:
            band_info = " / ".join(value for value in (pcell_band, scell_band) if value)

        dl_bandwidth = None
        if pcell_bw or scell_bw:
            dl_bandwidth = " / ".join(value for value in (pcell_bw, scell_bw) if value)

        cell_id = self.first_value(signal.get("cell_id"), signal.get("Z5g_CELL_ID"))

        return {
            "router_vendor": self.vendor,
            "router_name": self.router_name,
            "network_type": self.clean_value(signal.get("network_type")) or network_type,
            "operator": operator_name,
            "pci": self.first_value(signal.get("Z_PCI"), signal.get("Z5g_PCI")),
            "cell_id": cell_id,
            "enodeb_id": self.first_value(
                signal.get("enodeb_id"),
                signal.get("eNBID"),
                self._derive_lte_enodeb_id(cell_id),
            ),
            "rsrp": self.first_value(signal.get("lte_rsrp"), signal.get("Z5g_rsrp")),
            "rsrq": self.first_value(signal.get("rsrq"), signal.get("Z_rsrq"), signal.get("lte_rsrq")),
            "sinr": self.first_value(signal.get("sinr"), signal.get("Z5g_SINR"), signal.get("Z5g_snr")),
            "rssi": self.clean_value(signal.get("rssi")),
            "band": self.first_value(signal.get("ZCELLINFO_band"), signal.get("Z5g_CELLINFO_band")),
            "band_info": band_info,
            "earfcn": self.first_value(
                signal.get("Z_dl_earfcn"),
                signal.get("lte_ca_pcell_arfcn"),
                signal.get("Z5g_dlEarfcn"),
            ),
            "ul_bandwidth": None,
            "dl_bandwidth": dl_bandwidth,
            "tac": self.first_value(signal.get("lte_tac"), signal.get("tac")),
            "plmn": self._plmn(signal),
            "rrc_status": None,
            "txpower": None,
            "transmode": self.first_value(
                signal.get("transmode"),
                signal.get("trans_mode"),
                signal.get("lte_transmode"),
            ),
            "cqi0": self.first_value(signal.get("cqi0"), signal.get("cqi"), signal.get("lte_cqi")),
            "raw_signal": signal,
        }
