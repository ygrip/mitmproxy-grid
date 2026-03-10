from pydantic import BaseModel, Field
from typing import Any, Optional, List, Dict


# ── Shared building blocks ───────────────────────────────────────────────────


class HeaderModification(BaseModel):
    """Add, overwrite, or remove HTTP headers."""

    set: Optional[Dict[str, str]] = Field(
        None, description="Header name-value pairs to set or overwrite"
    )
    remove: Optional[List[str]] = Field(
        None, description="Header names to remove"
    )


class ParamModification(BaseModel):
    """Add, overwrite, or remove URL query parameters."""

    set: Optional[Dict[str, str]] = Field(
        None, description="Query-param name-value pairs to set or overwrite"
    )
    remove: Optional[List[str]] = Field(
        None, description="Query-param names to remove"
    )


class BodyReplaceSchema(BaseModel):
    """Substring find-and-replace inside a body."""

    from_: str = Field(..., description="Substring to search for")
    to: str = Field(..., description="Replacement string")


# ── Match ────────────────────────────────────────────────────────────────────


class RuleMatch(BaseModel):
    """Criteria a flow must satisfy for the rule to fire."""

    urlContains: Optional[str] = Field(
        None, description="Substring that must appear in the request URL"
    )
    urlPattern: Optional[str] = Field(
        None, description="Regex pattern tested against the full request URL"
    )
    method: Optional[str] = Field(
        None, description="HTTP method (GET, POST, PUT, DELETE, PATCH, …)"
    )
    contentType: Optional[str] = Field(
        None, description="Substring match on the request Content-Type header"
    )
    responseContentType: Optional[str] = Field(
        None,
        description="Substring match on the response Content-Type header (evaluated during response phase only)",
    )


# ── Actions ──────────────────────────────────────────────────────────────────


class RequestModification(BaseModel):
    """Modifications applied to the outgoing request."""

    headers: Optional[HeaderModification] = Field(
        None, description="Request header modifications"
    )
    params: Optional[ParamModification] = Field(
        None, description="Query parameter modifications"
    )
    body: Optional[Any] = Field(
        None, description="Replace the entire request body (string or JSON object)"
    )
    bodyBase64: Optional[str] = Field(
        None,
        description="Base64-encoded body content for binary payloads. "
        "Takes precedence over 'body' when both are set. "
        "Large payloads are automatically externalized to disk.",
    )
    bodyFile: Optional[str] = Field(
        None,
        description="Server-managed path to externalized binary body file. "
        "Set automatically when bodyBase64 is provided; do not set manually.",
    )
    bodyBase64Size: Optional[int] = Field(
        None,
        description="Decoded size in bytes of the externalized bodyBase64 content.",
    )
    bodyReplace: Optional[BodyReplaceSchema] = Field(
        None, description="Substring replacement inside the request body"
    )


class ResponseModification(BaseModel):
    """Modifications applied to the incoming response."""

    statusCode: Optional[int] = Field(
        None, description="Override the HTTP response status code"
    )
    headers: Optional[HeaderModification] = Field(
        None, description="Response header modifications"
    )
    body: Optional[Any] = Field(
        None, description="Replace the entire response body (string or JSON object)"
    )
    bodyBase64: Optional[str] = Field(
        None,
        description="Base64-encoded body content for binary responses (images, fonts, etc.). "
        "Takes precedence over 'body' when both are set. "
        "Large payloads are automatically externalized to disk.",
    )
    bodyFile: Optional[str] = Field(
        None,
        description="Server-managed path to externalized binary body file. "
        "Set automatically when bodyBase64 is provided; do not set manually.",
    )
    bodyBase64Size: Optional[int] = Field(
        None,
        description="Decoded size in bytes of the externalized bodyBase64 content.",
    )
    bodyReplace: Optional[BodyReplaceSchema] = Field(
        None, description="Substring replacement inside the response body"
    )


class RuleAction(BaseModel):
    """What to do when the rule matches.  Both fields are optional and can be
    combined so a single rule modifies both the request and the response."""

    modifyRequest: Optional[RequestModification] = Field(
        None, description="Modifications applied during the request phase"
    )
    modifyResponse: Optional[ResponseModification] = Field(
        None, description="Modifications applied during the response phase"
    )


# ── Rule ─────────────────────────────────────────────────────────────────────


class RuleCreate(BaseModel):
    """Create a new interception rule."""

    enabled: bool = Field(True, description="Whether the rule is active")
    priority: int = Field(
        0, description="Higher-priority rules are evaluated first"
    )
    match: RuleMatch = Field(
        default_factory=RuleMatch, description="Flow-matching criteria"
    )
    action: RuleAction = Field(..., description="Modifications to apply")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "enabled": True,
                    "priority": 10,
                    "match": {
                        "urlContains": "api.example.com",
                        "method": "GET",
                    },
                    "action": {
                        "modifyResponse": {
                            "statusCode": 200,
                            "headers": {"set": {"X-Mock": "true"}},
                            "body": '{"mocked": true}',
                        }
                    },
                },
                {
                    "enabled": True,
                    "priority": 5,
                    "match": {
                        "urlPattern": ".*\\/api\\/v[0-9]+\\/users.*",
                        "contentType": "application/json",
                    },
                    "action": {
                        "modifyRequest": {
                            "headers": {
                                "set": {"Authorization": "Bearer test-token"},
                            },
                            "params": {"set": {"debug": "true"}},
                        },
                        "modifyResponse": {
                            "bodyReplace": {
                                "from_": "prod-db",
                                "to": "test-db",
                            },
                        },
                    },
                },
                {
                    "enabled": True,
                    "priority": 10,
                    "match": {"urlContains": "avatar.png"},
                    "action": {
                        "modifyResponse": {
                            "statusCode": 200,
                            "headers": {
                                "set": {
                                    "Content-Type": "image/png",
                                    "Cache-Control": "no-cache",
                                }
                            },
                            "bodyBase64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNgA=",
                        }
                    },
                },
            ]
        }
    }


class RuleResponse(BaseModel):
    index: int
    enabled: bool
    priority: int
    match: RuleMatch
    action: RuleAction


# ── Instance ─────────────────────────────────────────────────────────────────


class InstanceSummary(BaseModel):
    instanceId: str
    port: int
    status: str
    createdAt: str
    uptimeSeconds: float
    ttl: int = Field(description="Configured lifespan in seconds")
    remainingSeconds: float = Field(description="Seconds until auto-destruction")
    ruleCount: int
    clientIps: List[str] = Field(
        default_factory=list,
        description="Unique client IP addresses that have connected through this proxy",
    )


class InstanceDetail(BaseModel):
    instanceId: str
    port: int
    status: str
    createdAt: str
    uptimeSeconds: float
    ttl: int
    remainingSeconds: float
    rules: List[RuleResponse]
    clientIps: List[str] = Field(
        default_factory=list,
        description="Unique client IP addresses that have connected through this proxy",
    )


# ── Responses ────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    instances: int
    usedPorts: List[int]
    availableSlots: int
    portRange: str
    defaultTtl: int = Field(description="Default instance lifespan in seconds")


class CreateInstanceResponse(BaseModel):
    instanceId: str
    port: int
    status: str
    ttl: int
    expiresAt: str


class RenewResponse(BaseModel):
    status: str
    message: str
    ttl: int
    expiresAt: str
    remainingSeconds: float


class MessageResponse(BaseModel):
    status: str
    message: Optional[str] = None
