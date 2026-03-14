"""URL validation — SSRF prevention for benchmark execution."""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

log = logging.getLogger("prowl_bench.sandbox")

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

BLOCKED_DOMAINS = {
    "localhost", "metadata.google.internal", "169.254.169.254", "metadata.google.com",
}

SUSPICIOUS_PATTERNS = [
    re.compile(r"https?://[^/]*webhook\.site", re.I),
    re.compile(r"https?://[^/]*requestbin", re.I),
    re.compile(r"https?://[^/]*ngrok", re.I),
    re.compile(r"https?://[^/]*burpcollaborator", re.I),
    re.compile(r"https?://[^/]*interact\.sh", re.I),
    re.compile(r"https?://[^/]*oastify\.com", re.I),
    re.compile(r"https?://[^/]*pipedream", re.I),
]


class SandboxViolation(Exception):
    """Raised when a benchmark request violates sandbox rules."""
    pass


def validate_url(url: str, service_base_url: str | None = None) -> str:
    """Validate a URL is safe to request during benchmarking."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SandboxViolation(f"Blocked scheme: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise SandboxViolation(f"No hostname in URL: {url}")

    if hostname.lower() in BLOCKED_DOMAINS:
        raise SandboxViolation(f"Blocked domain: {hostname}")

    try:
        ips = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in ips:
            ip = ipaddress.ip_address(sockaddr[0])
            for network in BLOCKED_NETWORKS:
                if ip in network:
                    raise SandboxViolation(f"Blocked IP: {ip} ({hostname}) in {network}")
    except socket.gaierror:
        log.warning("Could not resolve hostname: %s", hostname)

    for pattern in SUSPICIOUS_PATTERNS:
        if pattern.search(url):
            raise SandboxViolation(f"Blocked suspicious URL: {url}")

    if service_base_url:
        service_host = urlparse(service_base_url).hostname
        if service_host and hostname.lower() != service_host.lower():
            allowed_cross = {"api.llama.fi", "api.coingecko.com"}
            if hostname.lower() not in allowed_cross:
                log.warning("Cross-domain: benchmark for %s targeting %s", service_host, hostname)

    return url
