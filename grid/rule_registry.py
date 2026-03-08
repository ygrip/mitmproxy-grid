import base64
import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("grid.rule_registry")

BLOB_DIR = Path("/data/blobs")


def _normalize(rule: dict) -> dict:
    """Convert legacy action format to the v2 structure."""
    action = rule.get("action", {})
    if action.get("type") == "MODIFY_RESPONSE" and "modifyResponse" not in action:
        mod = {}
        if action.get("bodyReplace"):
            mod["bodyReplace"] = action["bodyReplace"]
        rule = {**rule, "action": {"modifyResponse": mod} if mod else {}}
    return rule


def load_rules(rule_file: Path) -> list:
    if not rule_file.exists():
        return []
    try:
        rules = json.loads(rule_file.read_text())
        return [_normalize(r) for r in rules]
    except (json.JSONDecodeError, IOError):
        return []


def save_rules(rule_file: Path, rules: list):
    rule_file.parent.mkdir(parents=True, exist_ok=True)
    rule_file.write_text(json.dumps(rules, indent=2))


def delete_rule(rule_file: Path, index: int) -> bool:
    rules = load_rules(rule_file)
    if 0 <= index < len(rules):
        removed = rules.pop(index)
        _cleanup_blobs(removed)
        save_rules(rule_file, rules)
        return True
    return False


def toggle_rule(rule_file: Path, index: int):
    """Toggle enabled state.  Returns the new state, or None if out of range."""
    rules = load_rules(rule_file)
    if 0 <= index < len(rules):
        rules[index]["enabled"] = not rules[index].get("enabled", True)
        save_rules(rule_file, rules)
        return rules[index]["enabled"]
    return None


# ── Blob externalization ─────────────────────────────────────────────────────


def externalize_blobs(rule: dict, instance_id: str) -> dict:
    """Decode bodyBase64 fields, write raw bytes to /data/blobs/, replace with bodyFile reference.

    This keeps rule JSON files small — the interceptor reads the binary file
    only when a rule actually matches, and the file is cached in memory.
    """
    for key in ("modifyRequest", "modifyResponse"):
        mod = rule.get("action", {}).get(key)
        if not mod or not mod.get("bodyBase64"):
            continue
        try:
            raw = base64.b64decode(mod["bodyBase64"])
        except Exception:
            log.warning("Invalid base64 in %s for instance %s", key, instance_id)
            continue
        content_hash = hashlib.sha256(raw).hexdigest()[:16]
        blob_name = f"{instance_id}_{content_hash}.bin"
        BLOB_DIR.mkdir(parents=True, exist_ok=True)
        blob_path = BLOB_DIR / blob_name
        blob_path.write_bytes(raw)
        mod["bodyFile"] = str(blob_path)
        mod["bodyBase64Size"] = len(raw)
        del mod["bodyBase64"]
        log.info(
            "Externalized %d bytes to %s for instance %s",
            len(raw), blob_name, instance_id[:8],
        )
    return rule


def _cleanup_blobs(rule: dict):
    """Remove blob files referenced by a rule."""
    for key in ("modifyRequest", "modifyResponse"):
        mod = rule.get("action", {}).get(key)
        if not mod:
            continue
        body_file = mod.get("bodyFile")
        if body_file:
            try:
                Path(body_file).unlink(missing_ok=True)
                log.info("Cleaned up blob %s", body_file)
            except Exception:
                pass


def cleanup_all_blobs(rule_file: Path):
    """Remove all blob files referenced by rules in a rule file."""
    for rule in load_rules(rule_file):
        _cleanup_blobs(rule)
