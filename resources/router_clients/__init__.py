from .base import BaseRouterClient
from .huawei import HuaweiRouterClient
from .zte import ZteRouterClient


def create_router_client(vendor, **kwargs):
    normalized_vendor = (vendor or "huawei").strip().lower()

    if normalized_vendor == "huawei":
        return HuaweiRouterClient(**kwargs)

    if normalized_vendor == "zte":
        return ZteRouterClient(**kwargs)

    raise ValueError(f"Unsupported router vendor: {vendor}")


__all__ = [
    "BaseRouterClient",
    "HuaweiRouterClient",
    "ZteRouterClient",
    "create_router_client",
]
