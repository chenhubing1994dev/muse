"""Conservative source-group diversity hints; never an independence verdict."""
from __future__ import annotations

from urllib.parse import urlsplit


def source_group_diversity_hint(url: str) -> str:
    """Group origins heuristically for diversity display only, not ownership or review."""
    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return host
    multi_label_suffixes = frozenset({
        "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.nz",
        "com.br", "com.cn", "com.sg", "co.jp", "co.in", "com.mx", "com.tr",
        "co.kr", "com.tw", "com.hk",
    })
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in multi_label_suffixes else suffix
