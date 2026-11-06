"""What a long OpenAPI spec says about where to call.

`analyze_service` used to send the LLM the first 5000 characters of the spec.
On a 90 KB spec the model saw `servers`, two paths, and guessed the rest:
`/v1/current` on an API whose real path is `/current.json` under
`https://api.weatherapi.com/v1`. Every probe then failed the same way, and the
score measured our guess instead of the service.

So the part that decides the URLs is extracted deterministically and goes
first, whatever the spec's length. The declared chain then checks the plan:
endpoints are resolved against what the spec lists, never guessed.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger("prowl_bench.spec_digest")

# YAML parsing blocks the event loop; a spec this big is not worth it.
MAX_PARSE_CHARS = 2_000_000
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_EXTENSION = re.compile(r"\.(json|xml|ya?ml|csv)$", re.I)


def parse_spec(text: str | None) -> dict | None:
    """The spec as a dict when it is OpenAPI or Swagger, else None."""
    if not text or len(text) > MAX_PARSE_CHARS:
        return None
    obj: Any = None
    if text.lstrip()[:1] in ("{", "["):
        try:
            obj = json.loads(text)
        except ValueError:
            pass
    if obj is None:
        try:
            import yaml
            obj = yaml.safe_load(text)
        except Exception:  # pyyaml is optional
            return None
    if isinstance(obj, dict) and isinstance(obj.get("paths"), dict) and obj["paths"]:
        return obj
    return None


def _operations(spec: dict) -> list[tuple[str, list[tuple[str, dict]]]]:
    out = []
    for path, item in spec["paths"].items():
        if not isinstance(item, dict):
            continue
        ops = [(m, item[m]) for m in HTTP_METHODS if isinstance(item.get(m), dict)]
        out.append((str(path), ops))
    return out


def declared_servers(spec: dict) -> list[str]:
    """Absolute base URLs the spec declares, in its order."""
    urls: list[str] = []
    for server in spec.get("servers") or []:
        url = server.get("url") if isinstance(server, dict) else None
        if isinstance(url, str) and url.startswith(("http://", "https://")) and "{" not in url:
            urls.append(url.rstrip("/"))
    host = spec.get("host")
    if isinstance(host, str) and host and "{" not in host:
        schemes = spec.get("schemes") or ["https"]
        scheme = "https" if "https" in schemes else str(schemes[0])
        urls.append(f"{scheme}://{host}{str(spec.get('basePath') or '').rstrip('/')}")
    return urls


def _operation_line(method: str, path: str, op: dict) -> str:
    line = f"{method.upper()} {path}"
    summary = str(op.get("summary") or op.get("operationId") or "").strip()
    if summary:
        line += f" - {summary[:120]}"
    names = [
        ("*" if p.get("required") else "") + str(p.get("name"))
        for p in op.get("parameters") or []
        if isinstance(p, dict) and p.get("name")
    ]
    if names:
        line += f" (params: {', '.join(names[:12])})"
    return line


def spec_digest(text: str, limit: int = 15000) -> str:
    """The spec for the LLM: servers and every path first, then the raw head.

    Anything that is not a parseable spec comes back as it arrived. Every path
    is kept even when the summaries no longer fit: a missing summary costs
    detail, a missing path costs a guessed URL.
    """
    spec = parse_spec(text)
    if spec is None:
        return text

    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    lines = [
        "# Digest of the spec below: these servers and paths are complete.",
        f"title: {info.get('title', '')}",
        f"servers: {', '.join(declared_servers(spec)) or 'none declared'}",
        "paths are relative to the server URL:",
    ]
    operations = _operations(spec)
    bare = [f"{','.join(m.upper() for m, _ in ops)} {path}" for path, ops in operations]
    full = [_operation_line(m, path, op) for path, ops in operations for m, op in ops]
    head = "\n".join(lines)
    chosen = full if len(head) + sum(len(x) + 1 for x in full) <= limit else bare
    digest = "\n".join([head, *chosen])[:limit]

    room = limit - len(digest) - 40
    if room > 0:
        digest += f"\n\n# Start of the raw spec\n{text[:room]}"
    return digest


def _usable_server(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (
        url.startswith("https://")
        and "." in host
        and not host.endswith((".local", ".internal", ".example", ".test"))
        and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host)
    )


def declared_base_url(spec: dict, current: str | None) -> str | None:
    """The server to call, or None to keep `current`.

    A base the spec lists is left alone, since it may be one of several on
    purpose. Otherwise the first public https server wins over a guess.
    """
    servers = declared_servers(spec)
    if current and current.rstrip("/") in servers:
        return None
    return next((s for s in servers if _usable_server(s)), None)


def _path_regex(path: str) -> re.Pattern:
    parts = re.split(r"\{[^/}]*\}", path)
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "$")


def _stem(path: str) -> str:
    return _EXTENSION.sub("", path.rstrip("/"))


def resolve_endpoint(endpoint: str, paths: list[str], base_path: str = "") -> str | None:
    """The spec's form of `endpoint`, or None when the spec does not list it.

    Only two slips are corrected: the base path repeated in the endpoint, and
    a missing or wrong format extension when exactly one path fits. A path the
    spec does not have is left to fail, because a planned 404 test is real.
    """
    path, sep, query = endpoint.partition("?")
    if not path.startswith("/"):
        return None
    base_path = base_path.rstrip("/")
    candidates = [path]
    if base_path and path.startswith(base_path + "/"):
        candidates.append(path[len(base_path):])

    for candidate in reversed(candidates):
        if any(_path_regex(p).match(candidate) for p in paths):
            return candidate + sep + query

    for candidate in reversed(candidates):
        hits = [p for p in paths if _path_regex(_stem(p)).match(_stem(candidate))]
        if len(hits) == 1:
            suffix = hits[0][len(_stem(hits[0])):]
            return _stem(candidate) + suffix + sep + query
    return None


def align_plan(tests: list[dict], spec: dict, base_url: str) -> int:
    """Rewrite planned endpoints to the spec's paths; returns how many changed."""
    paths = [str(p) for p in spec["paths"]]
    base_path = urlparse(base_url or "").path
    changed = 0
    for test in tests:
        endpoint = test.get("endpoint")
        if not isinstance(endpoint, str):
            continue
        resolved = resolve_endpoint(endpoint, paths, base_path)
        if resolved and resolved != endpoint:
            log.info("plan endpoint %s resolved to %s", endpoint, resolved)
            test["endpoint"] = resolved
            changed += 1
    return changed
