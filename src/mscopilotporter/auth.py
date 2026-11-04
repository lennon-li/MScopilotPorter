import os
import hmac
import time
import secrets
import logging
import socket

from mscopilotporter import paths
from fnmatch import fnmatch
from collections import defaultdict
from starlette.types import ASGIApp, Scope, Receive, Send
from starlette.responses import JSONResponse

logger = logging.getLogger("mscopilotporter.auth")

SECRETS_FILE = str(paths.secrets_file())
MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10 MB
RATE_LIMIT_WINDOW = 60.0  # seconds
RATE_LIMIT_MAX_REQUESTS = 120  # requests per window

def get_or_create_api_key() -> str:
    """Retrieve API key from environment or secrets file, creating it if needed."""
    env_key = os.environ.get("MSCP_KEY") or os.environ.get("M3_MCP_KEY")
    if env_key:
        return env_key.strip()

    os.makedirs(os.path.dirname(SECRETS_FILE), mode=0o700, exist_ok=True)
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith(("MSCP_KEY=", "M3_MCP_KEY=")):
                    return line.split("=", 1)[1].strip()
                elif line and not line.startswith("#") and "=" not in line:
                    return line.strip()

    # Generate new key
    new_key = secrets.token_urlsafe(32)
    with open(SECRETS_FILE, "w") as f:
        f.write(f"MSCP_KEY={new_key}\n")
    os.chmod(SECRETS_FILE, 0o600)
    logger.info("Generated new API key and saved to %s (mode 0600)", SECRETS_FILE)
    return new_key

API_KEY = get_or_create_api_key()

def get_allowed_hosts() -> list[str]:
    """Retrieve allowed host patterns."""
    hosts_env = os.environ.get("MSCP_ALLOWED_HOSTS") or os.environ.get("ALLOWED_HOSTS", "")
    hosts = [h.strip() for h in hosts_env.split(",") if h.strip()]
    # Loopback only by default. Add your tunnel hostname via MSCP_ALLOWED_HOSTS.
    default_hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
    return hosts + default_hosts

ALLOWED_HOSTS = get_allowed_hosts()

class RateLimiter:
    def __init__(self, max_requests: int = RATE_LIMIT_MAX_REQUESTS, window: float = RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.window = window
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        reqs = self.requests[client_ip]
        cutoff = now - self.window
        self.requests[client_ip] = [t for t in reqs if t > cutoff]
        if len(self.requests[client_ip]) >= self.max_requests:
            return False
        self.requests[client_ip].append(now)
        return True

rate_limiter = RateLimiter()

def is_host_allowed(host_header: str) -> bool:
    """Validate host header against allowed patterns."""
    if not host_header:
        return False
    host_clean = host_header.strip().lower()
    for pattern in ALLOWED_HOSTS:
        if fnmatch(host_clean, pattern.lower()):
            return True
        # Also match host without port if pattern has no port
        if ":" in host_clean and ":" not in pattern:
            hostname = host_clean.split(":", 1)[0]
            if fnmatch(hostname, pattern.lower()):
                return True
    return False

def verify_api_key(provided_key: str) -> bool:
    """Constant-time comparison of the provided API key."""
    if not provided_key:
        return False
    return hmac.compare_digest(provided_key.encode("utf-8"), API_KEY.encode("utf-8"))

import hashlib

class AuthAndSecurityMiddleware:
    """ASGI Middleware enforcing API key authentication, host validation, rate limiting, and size limits."""
    def __init__(self, app: ASGIApp):
        self.app = app

    def _log_rejection_diagnostics(self, headers: dict):
        raw_header = (headers.get(b"x-mscp-key") or headers.get(b"x-m3-mcp-key"))
        is_present = raw_header is not None
        val_len = len(raw_header) if is_present else 0
        header_hash_prefix = hashlib.sha256(raw_header).hexdigest()[:8] if is_present else "none"
        expected_hash_prefix = hashlib.sha256(API_KEY.encode("utf-8")).hexdigest()[:8]
        provided_key = raw_header.decode("utf-8", errors="replace") if is_present else ""
        matches = verify_api_key(provided_key)

        logger.warning(
            "AUTH_REJECTION_DIAGNOSTIC: "
            "x_mscp_key_present=%s | "
            "header_value_length=%d | "
            "header_sha256_prefix=%s | "
            "expected_sha256_prefix=%s | "
            "matches=%s",
            is_present,
            val_len,
            header_hash_prefix,
            expected_hash_prefix,
            matches
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))

        # 1. Host header validation
        host = headers.get(b"host", b"").decode("utf-8", errors="replace")
        if not is_host_allowed(host):
            self._log_rejection_diagnostics(headers)
            logger.warning("Rejected request with unauthorized Host: %s", host)
            resp = JSONResponse({"error": "Forbidden: Host header not allowed"}, status_code=403)
            await resp(scope, receive, send)
            return

        # 2. Rate limiting by client IP
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        if not rate_limiter.is_allowed(client_ip):
            self._log_rejection_diagnostics(headers)
            logger.warning("Rate limit exceeded for client: %s", client_ip)
            resp = JSONResponse({"error": "Too Many Requests: Rate limit exceeded"}, status_code=429)
            await resp(scope, receive, send)
            return

        # 3. Content-Length check
        content_length_str = headers.get(b"content-length", b"").decode("utf-8", errors="replace")
        if content_length_str.isdigit():
            if int(content_length_str) > MAX_REQUEST_SIZE:
                self._log_rejection_diagnostics(headers)
                logger.warning("Rejected payload exceeding max size: %s bytes", content_length_str)
                resp = JSONResponse({"error": "Payload Too Large"}, status_code=413)
                await resp(scope, receive, send)
                return

        # 4. API Key validation (X-MSCP-Key; legacy X-M3-MCP-Key accepted)
        # Note: headers in ASGI scope are lowercase bytes
        raw_header = (headers.get(b"x-mscp-key") or headers.get(b"x-m3-mcp-key"))
        provided_key = raw_header.decode("utf-8", errors="replace") if raw_header is not None else ""
        if not verify_api_key(provided_key):
            self._log_rejection_diagnostics(headers)
            resp = JSONResponse({"error": "Unauthorized: invalid or missing X-MSCP-Key"}, status_code=401)
            await resp(scope, receive, send)
            return

        # Health endpoint
        if scope.get("path") == "/health":
            resp = JSONResponse({
                "status": "healthy",
                "service": "mscopilotporter",
                "version": "1.0.0",
                "host": socket.gethostname(),
            })
            await resp(scope, receive, send)
            return

        # Rewrite root path '/' to '/mcp' so Copilot Studio root requests route to Streamable HTTP
        if scope.get("path") in ("/", ""):
            scope["path"] = "/mcp"
            if "raw_path" in scope:
                scope["raw_path"] = b"/mcp"

        # Passed all security checks, pass downstream
        await self.app(scope, receive, send)
