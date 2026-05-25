class BaseRouterClient:
    vendor = "unknown"

    def __init__(self, router_url, router_name=None, **kwargs):
        self.router_url = router_url.rstrip("/")
        self.router_name = router_name or self.vendor.title()

    def get_signal(self):
        raise NotImplementedError

    def normalize_signal(self, signal, operator_name=None, network_type=None):
        raise NotImplementedError

    @staticmethod
    def clean_value(value):
        if value is None or value == "":
            return None
        return value

    @staticmethod
    def first_value(*values):
        for value in values:
            if value is not None and value != "":
                return value
        return None

    def build_payload(self, operator_name=None, network_type=None):
        signal = self.get_signal()
        payload = self.normalize_signal(
            signal,
            operator_name=operator_name,
            network_type=network_type,
        )

        if not self.has_cellular_data(payload):
            raise RuntimeError(f"No cellular signal data returned by {self.vendor} router")

        return payload

    @classmethod
    def has_cellular_data(cls, payload):
        fields = (
            "pci",
            "cell_id",
            "enodeb_id",
            "rsrp",
            "rsrq",
            "sinr",
            "rssi",
            "band",
            "earfcn",
        )
        return any(cls.clean_value(payload.get(field)) is not None for field in fields)
