from mitmproxy import http, ctx
import base64
import json
import re
import time
from pathlib import Path

rule_file = None
_clients_file = None
_client_data = {}          # ip -> last_seen unix timestamp
_last_flush = 0.0

# ── Caching ──────────────────────────────────────────────────────────────────
_rule_cache_mtime = 0.0
_rule_cache_data = []
_blob_cache = {}           # path_str -> (mtime, bytes)


def load(loader):
    loader.add_option(
        name="rule_file",
        typespec=str,
        default="",
        help="Path to the JSON rule file for this instance",
    )


def configure(updated):
    global rule_file, _clients_file
    if "rule_file" in updated and ctx.options.rule_file:
        rule_file = Path(ctx.options.rule_file)
        _clients_file = rule_file.with_name(rule_file.stem + "_clients.json")


def _load_rules():
    """Load rules with mtime-based caching to avoid re-parsing JSON on every request."""
    global _rule_cache_mtime, _rule_cache_data
    if rule_file is None or not rule_file.exists():
        return []
    try:
        mtime = rule_file.stat().st_mtime
        if mtime == _rule_cache_mtime and _rule_cache_data:
            return _rule_cache_data
        rules = json.loads(rule_file.read_text())
        _rule_cache_mtime = mtime
        _rule_cache_data = rules
        return rules
    except (json.JSONDecodeError, IOError, OSError):
        return []


def _read_blob(path_str):
    """Read a binary blob file with mtime caching."""
    cached = _blob_cache.get(path_str)
    try:
        p = Path(path_str)
        mtime = p.stat().st_mtime
        if cached and cached[0] == mtime:
            return cached[1]
        data = p.read_bytes()
        _blob_cache[path_str] = (mtime, data)
        return data
    except (IOError, OSError):
        return None


# ── Client IP tracking ───────────────────────────────────────────────────────


def _track_client(flow):
    global _last_flush
    try:
        ip = flow.client_conn.peername[0]
    except Exception:
        return
    now = time.time()
    is_new = ip not in _client_data
    _client_data[ip] = now
    if is_new or now - _last_flush > 5:
        _flush_clients()
        _last_flush = now


def _flush_clients():
    if _clients_file is None:
        return
    try:
        data = [{"ip": ip, "lastSeen": round(ts, 1)} for ip, ts in _client_data.items()]
        _clients_file.write_text(json.dumps(data))
    except Exception:
        pass


# ── Matching ─────────────────────────────────────────────────────────────────


def _matches(rule, flow, phase="response"):
    if not rule.get("enabled", True):
        return False

    m = rule.get("match", {})
    url = flow.request.pretty_url

    if m.get("urlContains") and m["urlContains"] not in url:
        return False

    if m.get("urlPattern"):
        try:
            if not re.search(m["urlPattern"], url):
                return False
        except re.error:
            return False

    if m.get("method") and m["method"].upper() != flow.request.method:
        return False

    if m.get("contentType"):
        ct = flow.request.headers.get("content-type", "")
        if m["contentType"].lower() not in ct.lower():
            return False

    if phase == "response" and m.get("responseContentType") and flow.response:
        ct = flow.response.headers.get("content-type", "")
        if m["responseContentType"].lower() not in ct.lower():
            return False

    return True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _apply_headers(headers, mod):
    if not mod:
        return
    for k, v in (mod.get("set") or {}).items():
        headers[k] = v
    for k in (mod.get("remove") or []):
        if k in headers:
            del headers[k]


def _apply_body(message, mod):
    """Apply body modifications.

    Precedence: bodyFile (externalized) > bodyBase64 (inline) > body (text) > bodyReplace (substring).
    """
    if mod.get("bodyFile") is not None:
        data = _read_blob(mod["bodyFile"])
        if data is not None:
            message.content = data
        return
    if mod.get("bodyBase64") is not None:
        message.content = base64.b64decode(mod["bodyBase64"])
        return
    if mod.get("body") is not None:
        body_val = mod["body"]
        if isinstance(body_val, (dict, list)):
            message.text = json.dumps(body_val, ensure_ascii=False)
        else:
            message.text = str(body_val)
        return
    repl = mod.get("bodyReplace")
    if not repl:
        return
    original = repl.get("from_", repl.get("from", ""))
    replacement = repl.get("to", "")
    current = message.text
    if original and current:
        message.text = current.replace(original, replacement)


# ── Hooks ────────────────────────────────────────────────────────────────────


def request(flow: http.HTTPFlow):
    _track_client(flow)

    rules = _load_rules()
    rules.sort(key=lambda r: r.get("priority", 0), reverse=True)

    for rule in rules:
        if not _matches(rule, flow, phase="request"):
            continue

        mod = rule.get("action", {}).get("modifyRequest")
        if not mod:
            continue

        _apply_headers(flow.request.headers, mod.get("headers"))

        params_mod = mod.get("params")
        if params_mod:
            for k, v in (params_mod.get("set") or {}).items():
                flow.request.query[k] = v
            for k in (params_mod.get("remove") or []):
                if k in flow.request.query:
                    del flow.request.query[k]

        _apply_body(flow.request, mod)


def response(flow: http.HTTPFlow):
    rules = _load_rules()
    rules.sort(key=lambda r: r.get("priority", 0), reverse=True)

    for rule in rules:
        if not _matches(rule, flow, phase="response"):
            continue

        action = rule.get("action", {})

        # Legacy v1 format
        if action.get("type") == "MODIFY_RESPONSE":
            if flow.response and action.get("bodyReplace"):
                repl = action["bodyReplace"]
                orig = repl.get("from_", repl.get("from", ""))
                to = repl.get("to", "")
                if orig and flow.response.text:
                    flow.response.text = flow.response.text.replace(orig, to)
            continue

        mod = action.get("modifyResponse")
        if not mod or not flow.response:
            continue

        if mod.get("statusCode") is not None:
            flow.response.status_code = mod["statusCode"]

        _apply_headers(flow.response.headers, mod.get("headers"))

        _apply_body(flow.response, mod)
