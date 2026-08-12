from pydantic import BaseModel, Field
from typing import Any, Optional, List, Dict


class HeaderModification(BaseModel):
    set: Optional[Dict[str, str]] = Field(None, description="Header name-value pairs to set or overwrite")
    remove: Optional[List[str]] = Field(None, description="Header names to remove")


class ParamModification(BaseModel):
    set: Optional[Dict[str, str]] = Field(None, description="Query-param name-value pairs to set or overwrite")
    remove: Optional[List[str]] = Field(None, description="Query-param names to remove")


class BodyReplaceSchema(BaseModel):
    from_: str = Field(..., description="Substring to search for")
    to: str = Field(..., description="Replacement string")


class RuleMatch(BaseModel):
    urlContains: Optional[str] = Field(None, description="Substring that must appear in the request URL")
    urlPattern: Optional[str] = Field(None, description="Regex pattern tested against the full request URL")
    method: Optional[str] = Field(None, description="HTTP method (GET, POST, PUT, DELETE, PATCH, …)")
    contentType: Optional[str] = Field(None, description="Substring match on the request Content-Type header")
    responseContentType: Optional[str] = Field(
        None,
        description="Substring match on the response Content-Type header (evaluated during response phase only)",
    )


class RequestModification(BaseModel):
    headers: Optional[HeaderModification] = Field(None, description="Request header modifications")
    params: Optional[ParamModification] = Field(None, description="Query parameter modifications")
    body: Optional[Any] = Field(None, description="Replace the entire request body (string or JSON object)")
    bodyBase64: Optional[str] = Field(
        None,
        description="Base64-encoded body content for binary payloads. Takes precedence over 'body' when both are set. Large payloads are automatically externalized to disk.",
    )
    bodyFile: Optional[str] = Field(
        None,
        description="Server-managed path to externalized binary body file. Set automatically when bodyBase64 is provided; do not set manually.",
    )
    bodyBase64Size: Optional[int] = Field(None, description="Decoded size in bytes of the externalized bodyBase64 content.")
    bodyReplace: Optional[BodyReplaceSchema] = Field(None, description="Substring replacement inside the request body")


class ResponseModification(BaseModel):
    statusCode: Optional[int] = Field(None, description="Override the HTTP response status code")
    headers: Optional[HeaderModification] = Field(None, description="Response header modifications")
    body: Optional[Any] = Field(None, description="Replace the entire response body (string or JSON object)")
    bodyBase64: Optional[str] = Field(
        None,
        description="Base64-encoded body content for binary responses (images, fonts, etc.). Takes precedence over 'body' when both are set. Large payloads are automatically externalized to disk.",
    )
    bodyFile: Optional[str] = Field(
        None,
        description="Server-managed path to externalized binary body file. Set automatically when bodyBase64 is provided; do not set manually.",
    )
    bodyBase64Size: Optional[int] = Field(None, description="Decoded size in bytes of the externalized bodyBase64 content.")
    bodyReplace: Optional[BodyReplaceSchema] = Field(None, description="Substring replacement inside the response body")


class RuleAction(BaseModel):
    modifyRequest: Optional[RequestModification] = Field(None, description="Modifications applied during the request phase")
    modifyResponse: Optional[ResponseModification] = Field(None, description="Modifications applied during the response phase")


class RuleCreate(BaseModel):
    enabled: bool = Field(True, description="Whether the rule is active")
    priority: int = Field(0, description="Higher-priority rules are evaluated first")
    match: RuleMatch = Field(default_factory=RuleMatch, description="Flow-matching criteria")
    action: RuleAction = Field(..., description="Modifications to apply")


class RuleResponse(BaseModel):
    index: int
    enabled: bool
    priority: int
    match: RuleMatch
    action: RuleAction


class ProxyEndpointFields(BaseModel):
    port: int
    proxyHost: Optional[str] = None
    proxyPort: Optional[int] = None
    proxyUrl: Optional[str] = None
    workerId: Optional[str] = None


class InstanceSummary(ProxyEndpointFields):
    instanceId: str
    status: str
    createdAt: str
    uptimeSeconds: float
    ttl: int = Field(description="Configured lifespan in seconds")
    remainingSeconds: float = Field(description="Seconds until auto-destruction")
    ruleCount: int
    clientIps: List[str] = Field(default_factory=list, description="Unique client IP addresses that have connected through this proxy")


class InstanceDetail(ProxyEndpointFields):
    instanceId: str
    status: str
    createdAt: str
    uptimeSeconds: float
    ttl: int
    remainingSeconds: float
    rules: List[RuleResponse]
    clientIps: List[str] = Field(default_factory=list, description="Unique client IP addresses that have connected through this proxy")


class WorkerRegistrationRequest(BaseModel):
    workerId: str
    apiUrl: str
    proxyHost: str
    availableSlots: int
    instances: int
    gridVersion: str
    apiVersion: str


class WorkerInfo(WorkerRegistrationRequest):
    lastSeenSeconds: float = 0.0


class HealthResponse(BaseModel):
    status: str
    instances: int
    usedPorts: List[int] = Field(default_factory=list)
    availableSlots: int
    portRange: str
    defaultTtl: int = Field(description="Default instance lifespan in seconds")
    gridVersion: str = "dev"
    apiVersion: str = "2"
    mode: str = "standalone"
    workerId: Optional[str] = None
    proxyHost: Optional[str] = None
    workers: List[WorkerInfo] = Field(default_factory=list)


class CreateInstanceResponse(ProxyEndpointFields):
    instanceId: str
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
