"""
AMS OTEL Monitor
=================
Standalone (non-Docker) process that:
  1. Receives an OTLP/HTTP-JSON log stream pushed by the otel-collector
     (see config/otel-collector/otel-collector-config.yaml, exporter
     `otlphttp/monitor`) at POST /v1/logs.
  2. Keeps only Warning/Error/Fatal severities, deduplicates repeats of the
     same underlying issue into a single record with an occurrence counter,
     and persists each issue as one JSON file under
     {MONITOR_DATA_DIR}/{SEVERITY}/{fingerprint}.json.
  3. Fires a webhook (if MONITOR_WEBHOOK_URL is set) on every new issue and
     every repeat occurrence.
  4. Exposes the same data to AI agents as MCP tools, and the whole app as
     one Streamable-HTTP MCP server on a single port.

Run with the standard (non-mingw) Windows Python, e.g.:
    py -3.11 -m pip install -r requirements.txt
    py -3.11 monitor.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("MONITOR_DATA_DIR", "./data")).resolve()
MAX_OCCURRENCES = int(os.environ.get("MONITOR_MAX_OCCURRENCES", "100"))
WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL") or None

# If set, every request to /mcp and /v1/logs must carry
# "Authorization: Bearer <this value>". /health stays open (no data exposed).
# Unset -> no auth at all; fine for localhost-only use, not for a shared LAN.
API_KEY = os.environ.get("MONITOR_API_KEY") or None
HOST = os.environ.get("MONITOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("MONITOR_PORT", "7787"))

# Only these severities are tracked; everything else (Information/Debug/
# Verbose/Trace) is dropped at the door. Fatal/Critical fold into ERROR.
SEVERITY_BUCKETS = {
    "Warning": "WARN",
    "Error": "ERROR",
    "Fatal": "ERROR",
    "Critical": "ERROR",
}


def bucket_for(severity_text: str | None) -> str | None:
    return SEVERITY_BUCKETS.get((severity_text or "").strip())


# ---------------------------------------------------------------------------
# OTLP/HTTP JSON parsing helpers
# ---------------------------------------------------------------------------

def unwrap_any_value(v: dict | None) -> Any:
    if not v:
        return None
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in v:
            return v[key]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "arrayValue" in v:
        return [unwrap_any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: unwrap_any_value(kv.get("value")) for kv in v["kvlistValue"].get("values", [])}
    return None


def attrs_to_dict(attrs: list | None) -> dict:
    return {a["key"]: unwrap_any_value(a.get("value")) for a in (attrs or [])}


def id_to_hex(value: str | None) -> str | None:
    """Normalize an OTLP trace/span ID to hex.

    Strict protobuf-JSON encodes byte fields as base64, but the OTel
    Collector's own pdata JSON marshaler (used by `encoding: json` on
    otlphttp) emits traceId/spanId as plain hex strings instead. Since hex
    digits are themselves valid base64 characters, blindly base64-decoding
    a hex string "succeeds" with the wrong length instead of failing loudly
    - so hex must be detected first, not treated as a fallback.
    """
    if not value:
        return None
    if all(c in "0123456789abcdefABCDEF" for c in value) and len(value) in (16, 32, 40, 64):
        return value.lower()
    try:
        return base64.b64decode(value).hex()
    except Exception:
        return None


def nano_to_iso(nano: Any) -> str:
    try:
        n = int(nano)
        if n > 0:
            return datetime.fromtimestamp(n / 1e9, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Message normalization (used for fingerprinting only)
# ---------------------------------------------------------------------------

# Serilog renders its templates before the OTLP sink sees them, and the
# collector doesn't forward message_template_text, so the only message we get
# is fully interpolated: "... /admin/products/all -> 500 in 17.9680 ms".
# Hashing that verbatim gives every request its own fingerprint - the elapsed
# time never repeats - so the occurrence counter stays at 1 forever and one
# recurring error grows a new issue file per request. Blanking the variable
# spans before hashing folds those back into a single issue. The rendered
# message is still stored verbatim, so nothing is lost for display.
#
# Deliberately NOT scrubbed: integers of three digits or fewer outside a path
# segment, so HTTP status codes stay distinguishing ("-> 500" must not
# collapse into "-> 503").
_NORMALIZE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # GUIDs (data-protection key IDs, correlation IDs, tenant IDs)
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<guid>"),
    # trace/span IDs, hashes, tokens
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    # ISO-8601 timestamps - must precede the numeric rules, which would
    # otherwise chew the date apart piece by piece
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    # numeric URL path segments: /products/42 -> /products/<num>
    (re.compile(r"(?<=/)\d+\b"), "<num>"),
    # decimals - elapsed ms, prices, percentages. Must precede the integer
    # rule, which would otherwise leave "3972.8728" as "<num>.8728".
    (re.compile(r"\b\d+\.\d+\b"), "<num>"),
    # long integers - IDs, byte counts, epoch values
    (re.compile(r"\b\d{4,}\b"), "<num>"),
)


def normalize_for_fingerprint(message: str) -> str:
    for pattern, placeholder in _NORMALIZE_RULES:
        message = pattern.sub(placeholder, message)
    return message


# ---------------------------------------------------------------------------
# Issue store (file-backed, deduplicated)
# ---------------------------------------------------------------------------

class IssueStore:
    def __init__(self, data_dir: Path, max_occurrences: int, webhook_url: str | None):
        self.data_dir = data_dir
        self.max_occurrences = max_occurrences
        self.webhook_url = webhook_url
        self.issues: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def load(self) -> None:
        if not self.data_dir.exists():
            return
        for severity_dir in self.data_dir.iterdir():
            if not severity_dir.is_dir():
                continue
            for f in severity_dir.glob("*.json"):
                try:
                    issue = json.loads(f.read_text(encoding="utf-8"))
                    self.issues[issue["fingerprint"]] = issue
                except Exception as e:
                    print(f"[monitor] skipping unreadable issue file {f}: {e}")

    @staticmethod
    def fingerprint_for(severity: str, service_name: str, exception_type: str | None,
                         scope_name: str | None, message: str) -> str:
        key = f"{service_name}|{severity}|{exception_type or scope_name or ''}|{normalize_for_fingerprint(message)}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _occ_summary(occ: dict) -> dict:
        return {k: occ.get(k) for k in ("timestamp", "trace_id", "span_id", "request_path")}

    def _write(self, issue: dict) -> None:
        d = self.data_dir / issue["severity"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{issue['fingerprint']}.json").write_text(json.dumps(issue, indent=2), encoding="utf-8")

    async def record(self, occ: dict) -> dict:
        fp = self.fingerprint_for(
            occ["severity"], occ["service_name"], occ.get("exception_type"),
            occ.get("scope_name"), occ["message"],
        )
        async with self._lock:
            existing = self.issues.get(fp)
            is_new = existing is None
            if is_new:
                issue = {
                    "fingerprint": fp,
                    "severity": occ["severity"],
                    "service_name": occ["service_name"],
                    "exception_type": occ.get("exception_type"),
                    "scope_name": occ.get("scope_name"),
                    "message": occ["message"],
                    "message_template": occ.get("message_template"),
                    "sample_stack_trace": occ.get("stack_trace"),
                    "first_seen": occ["timestamp"],
                    "last_seen": occ["timestamp"],
                    "count": 1,
                    "occurrences": [self._occ_summary(occ)],
                }
                self.issues[fp] = issue
            else:
                issue = existing
                issue["last_seen"] = occ["timestamp"]
                issue["count"] += 1
                issue["occurrences"].append(self._occ_summary(occ))
                if len(issue["occurrences"]) > self.max_occurrences:
                    issue["occurrences"] = issue["occurrences"][-self.max_occurrences:]
            self._write(issue)

        await self._fire_webhook(issue, occ, is_new)
        return issue

    async def _fire_webhook(self, issue: dict, occ: dict, is_new: bool) -> None:
        if not self.webhook_url:
            return
        payload = {
            "event": "new_issue" if is_new else "repeat_occurrence",
            "severity": issue["severity"],
            "service_name": issue["service_name"],
            "fingerprint": issue["fingerprint"],
            "exception_type": issue.get("exception_type"),
            "message": issue["message"],
            "count": issue["count"],
            "first_seen": issue["first_seen"],
            "last_seen": issue["last_seen"],
            "occurrence": self._occ_summary(occ),
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status >= 300:
                        print(f"[monitor] webhook POST returned HTTP {resp.status}")
        except Exception as e:
            print(f"[monitor] webhook POST failed: {e}")


store = IssueStore(DATA_DIR, MAX_OCCURRENCES, WEBHOOK_URL)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Requires 'Authorization: Bearer <MONITOR_API_KEY>' on every request except /health.

    No-op (everything passes) when MONITOR_API_KEY isn't set.
    """

    async def dispatch(self, request: Request, call_next):
        if not API_KEY or request.url.path == "/health":
            return await call_next(request)
        if request.headers.get("authorization") != f"Bearer {API_KEY}":
            return Response(status_code=401, content="unauthorized")
        return await call_next(request)


# ---------------------------------------------------------------------------
# MCP server: one process, one port, serves both /mcp and our custom routes
# ---------------------------------------------------------------------------

server = MCPServer(
    name="ams-otel-monitor",
    version="1.0.0",
    instructions=(
        "Tracks deduplicated Warning/Error/Fatal log issues observed on the "
        "progcoder-shop catalog-api, fed by the otel-collector's log pipeline."
    ),
)


# --- Shared read logic --------------------------------------------------
# The MCP tools (for AI agents) and the REST routes (for plain HTTP clients)
# both call these, so the two read interfaces can never drift apart.

def _list_issues(severity: str | None = None, limit: int = 50) -> list[dict]:
    items = list(store.issues.values())
    if severity:
        items = [i for i in items if i["severity"].upper() == severity.upper()]
    items.sort(key=lambda i: i["last_seen"], reverse=True)
    fields = ("fingerprint", "severity", "service_name", "exception_type", "message", "count", "first_seen", "last_seen")
    return [{k: i.get(k) for k in fields} for i in items[:limit]]


def _get_issue(fingerprint: str) -> dict:
    issue = store.issues.get(fingerprint)
    if not issue:
        return {"error": f"no issue found with fingerprint '{fingerprint}'"}
    return issue


def _get_stats() -> dict:
    items = list(store.issues.values())
    by_severity: dict[str, int] = {}
    total_occurrences = 0
    services: set[str] = set()
    for i in items:
        by_severity[i["severity"]] = by_severity.get(i["severity"], 0) + 1
        total_occurrences += i["count"]
        services.add(i["service_name"])
    return {
        "total_issues": len(items),
        "issues_by_severity": by_severity,
        "total_occurrences": total_occurrences,
        "services_seen": sorted(services),
    }


def _list_recent_occurrences(minutes: int = 60, severity: str | None = None) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    out = []
    for issue in store.issues.values():
        if severity and issue["severity"].upper() != severity.upper():
            continue
        for occ in issue["occurrences"]:
            ts = occ.get("timestamp")
            try:
                t = datetime.fromisoformat(ts)
            except (TypeError, ValueError):
                continue
            if t >= cutoff:
                out.append({
                    **occ,
                    "severity": issue["severity"],
                    "service_name": issue["service_name"],
                    "message": issue["message"],
                    "fingerprint": issue["fingerprint"],
                })
    out.sort(key=lambda o: o["timestamp"], reverse=True)
    return out


# --- MCP tools (AI agents, over Streamable-HTTP at /mcp) ------------------

@server.tool()
def list_issues(severity: str | None = None, limit: int = 50) -> list[dict]:
    """List tracked issues (deduplicated warning/error signatures), most recently seen first.

    Args:
        severity: optional filter, "WARN" or "ERROR".
        limit: max number of issues to return.
    """
    return _list_issues(severity, limit)


@server.tool()
def get_issue(fingerprint: str) -> dict:
    """Get full detail for one issue, including every recorded occurrence, by its fingerprint."""
    return _get_issue(fingerprint)


@server.tool()
def get_stats() -> dict:
    """Summary counts across all tracked issues: totals by severity, occurrence totals, services seen."""
    return _get_stats()


@server.tool()
def list_recent_occurrences(minutes: int = 60, severity: str | None = None) -> list[dict]:
    """Flat, non-deduplicated feed of individual occurrences within the last N minutes, most recent first.

    Args:
        minutes: how far back to look.
        severity: optional filter, "WARN" or "ERROR".
    """
    return _list_recent_occurrences(minutes, severity)


@server.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok", "issues_tracked": len(store.issues)})


@server.custom_route("/v1/logs", methods=["POST"])
async def receive_otlp_logs(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception as e:
        print(f"[monitor] rejecting malformed OTLP payload: {e}")
        return Response(status_code=400)

    tasks = []
    for rl in payload.get("resourceLogs", []):
        resource_attrs = attrs_to_dict((rl.get("resource") or {}).get("attributes"))
        service_name = resource_attrs.get("service.name", "unknown")

        for sl in rl.get("scopeLogs", []):
            scope_name = (sl.get("scope") or {}).get("name")

            for lr in sl.get("logRecords", []):
                try:
                    bucket = bucket_for(lr.get("severityText"))
                    if not bucket:
                        continue

                    attrs = attrs_to_dict(lr.get("attributes"))
                    body = unwrap_any_value(lr.get("body"))
                    exception_type = attrs.get("exception_type") or attrs.get("exception.type")
                    exception_message = (
                        attrs.get("exceptionMessage")
                        or attrs.get("exception_message")
                        or attrs.get("exception.message")
                    )
                    message_template = attrs.get("message_template_text")
                    message = exception_message or (body if isinstance(body, str) else None) or message_template or "(no message)"

                    occ = {
                        "severity": bucket,
                        "service_name": service_name,
                        "exception_type": exception_type,
                        "scope_name": scope_name,
                        "message": message,
                        "message_template": message_template,
                        "stack_trace": attrs.get("exception_stacktrace") or attrs.get("exception.stacktrace"),
                        "timestamp": nano_to_iso(lr.get("timeUnixNano") or lr.get("observedTimeUnixNano")),
                        "trace_id": id_to_hex(lr.get("traceId")),
                        "span_id": id_to_hex(lr.get("spanId")),
                        "request_path": attrs.get("RequestPath"),
                    }
                    tasks.append(store.record(occ))
                except Exception as e:
                    print(f"[monitor] skipping malformed log record: {e}")

    if tasks:
        await asyncio.gather(*tasks)

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# REST read API: same data as the MCP tools, for plain HTTP clients
# (dashboards, curl, non-agent services). These sit behind ApiKeyMiddleware
# like everything except /health, so each call needs the same
# "Authorization: Bearer <MONITOR_API_KEY>" the MCP and OTLP routes require.
# ---------------------------------------------------------------------------

def _int_param(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"query parameter '{name}' must be an integer, got {raw!r}")


@server.custom_route("/issues", methods=["GET"])
async def rest_list_issues(request: Request) -> Response:
    """GET /issues?severity=WARN|ERROR&limit=N  -> deduplicated issue list."""
    try:
        limit = _int_param(request, "limit", 50)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(_list_issues(request.query_params.get("severity"), limit))


@server.custom_route("/issues/{fingerprint}", methods=["GET"])
async def rest_get_issue(request: Request) -> Response:
    """GET /issues/{fingerprint}  -> full detail for one issue, or 404."""
    result = _get_issue(request.path_params["fingerprint"])
    if "error" in result:
        return JSONResponse(result, status_code=404)
    return JSONResponse(result)


@server.custom_route("/stats", methods=["GET"])
async def rest_get_stats(request: Request) -> Response:
    """GET /stats  -> summary counts across all tracked issues."""
    return JSONResponse(_get_stats())


@server.custom_route("/occurrences", methods=["GET"])
async def rest_list_recent_occurrences(request: Request) -> Response:
    """GET /occurrences?minutes=N&severity=WARN|ERROR  -> flat recent occurrence feed."""
    try:
        minutes = _int_param(request, "minutes", 60)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(_list_recent_occurrences(minutes, request.query_params.get("severity")))


def main() -> None:
    store.load()
    print(f"[monitor] loaded {len(store.issues)} existing issue(s) from {DATA_DIR}")
    print(f"[monitor] webhook: {'-> ' + WEBHOOK_URL if WEBHOOK_URL else 'not configured'}")
    print(f"[monitor] api key auth: {'ENABLED' if API_KEY else 'DISABLED (open to anyone who can reach this port)'}")
    print(f"[monitor] listening on http://{HOST}:{PORT}  (MCP: /mcp, REST: /issues /stats /occurrences, OTLP ingest: /v1/logs, health: /health)")

    # Building the app ourselves (rather than server.run()) so ApiKeyMiddleware
    # can wrap every route, including /mcp - this mirrors run_streamable_http_async
    # internally, just with the middleware attached before serving.
    app = server.streamable_http_app(host=HOST)
    app.add_middleware(ApiKeyMiddleware)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
