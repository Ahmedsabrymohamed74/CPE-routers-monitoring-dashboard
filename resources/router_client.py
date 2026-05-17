import re
import hmac
import hashlib
import secrets
import requests
import urllib3
import xml.etree.ElementTree as ET

urllib3.disable_warnings()


class RouterClient:
    def __init__(self, router_url, username, password):
        self.router_url = router_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.verify = False

        self.headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Connection": "keep-alive",
            "DNT": "1",
            "Origin": self.router_url,
            "Referer": self.router_url + "/",
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
            "_ResponseSource": "Broswer",
        }

    def _get_page_tokens(self):
        r = self.session.get(self.router_url + "/", headers=self.headers, timeout=10)
        r.raise_for_status()

        tokens = re.findall(
            r'<meta\s+name=["\']csrf_token["\']\s+content=["\']([^"\']+)["\']',
            r.text,
        )

        if not tokens:
            raise RuntimeError("No CSRF tokens found on router page")

        return tokens

    @staticmethod
    def _hmac_sha256_hex_message(key_string, message_hex):
        return hmac.new(
            key_string.encode("utf-8"),
            bytes.fromhex(message_hex),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _sha256_hex_bytes(hex_string):
        return hashlib.sha256(bytes.fromhex(hex_string)).hexdigest()

    @staticmethod
    def _xor_hex(hex_a, hex_b):
        a = bytes.fromhex(hex_a)
        b = bytes.fromhex(hex_b)
        return bytes(x ^ y for x, y in zip(a, b)).hex()

    @staticmethod
    def _xml_to_dict(xml_text):
        root = ET.fromstring(xml_text)
        return {child.tag: child.text for child in root}

    def login(self):
        tokens = self._get_page_tokens()
        challenge_token = tokens[0]

        firstnonce = secrets.token_hex(32)

        challenge_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<request>
  <username>{self.username}</username>
  <firstnonce>{firstnonce}</firstnonce>
  <mode>1</mode>
</request>'''

        challenge_headers = {
            **self.headers,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "__RequestVerificationToken": challenge_token,
        }

        r = self.session.post(
            f"{self.router_url}/api/user/challenge_login",
            headers=challenge_headers,
            data=challenge_xml,
            timeout=10,
        )

        if "<error>" in r.text:
            raise RuntimeError(f"Challenge failed: {r.text}")

        challenge = self._xml_to_dict(r.text)

        salt = challenge["salt"]
        iterations = int(challenge["iterations"])
        servernonce = challenge["servernonce"]

        auth_token = (
            r.headers.get("__RequestVerificationToken")
            or r.headers.get("__requestverificationtoken")
            or tokens[1]
        )

        salted_password_hex = hashlib.pbkdf2_hmac(
            "sha256",
            self.password.encode("utf-8"),
            bytes.fromhex(salt),
            iterations,
            dklen=32,
        ).hex()

        client_key_hex = self._hmac_sha256_hex_message(
            "Client Key",
            salted_password_hex,
        )

        stored_key_hex = self._sha256_hex_bytes(client_key_hex)

        auth_msg = f"{firstnonce},{servernonce},{servernonce}"

        client_signature_hex = self._hmac_sha256_hex_message(
            auth_msg,
            stored_key_hex,
        )

        clientproof = self._xor_hex(client_key_hex, client_signature_hex)

        auth_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<request>
  <clientproof>{clientproof}</clientproof>
  <finalnonce>{servernonce}</finalnonce>
</request>'''

        auth_headers = {
            **self.headers,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "__RequestVerificationToken": auth_token,
        }

        r = self.session.post(
            f"{self.router_url}/api/user/authentication_login",
            headers=auth_headers,
            data=auth_xml,
            timeout=10,
        )

        if "<error>" in r.text:
            raise RuntimeError(f"Authentication failed: {r.text}")

        return True

    def get_signal(self):
        r = self.session.get(
            f"{self.router_url}/api/device/signal",
            headers={
                **self.headers,
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=10,
        )

        if "<error>" in r.text:
            self.login()
            r = self.session.get(
                f"{self.router_url}/api/device/signal",
                headers=self.headers,
                timeout=10,
            )

        return self._xml_to_dict(r.text)

    def get_status(self):
        r = self.session.get(
            f"{self.router_url}/api/monitoring/status",
            headers={
                **self.headers,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Update-Cookie": "UpdateCookie",
            },
            timeout=10,
        )

        if "<error>" in r.text:
            self.login()
            r = self.session.get(
                f"{self.router_url}/api/monitoring/status",
                headers=self.headers,
                timeout=10,
            )

        return self._xml_to_dict(r.text)
