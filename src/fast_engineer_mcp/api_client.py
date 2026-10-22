"""The connection to the hosted fast.engineer endpoint.

``Remote`` owns one MCP client session over Streamable HTTP. The hosted
endpoint is stateless, so the session is cheap to rebuild: when a request
fails because the connection dropped, ``Remote.call`` reconnects once and
retries. Connecting is lazy, so the local server starts even while the
hosted service is unreachable and reports the problem per call instead.

``HttpErrorTranslator`` sits under the SDK's transport. fast.engineer answers
quota, kill-switch and authorization problems with plain JSON at an HTTP
error status (429, 503, 401, 403). The SDK would reduce those to the generic
"Server returned an error response"; the translator rewrites them into a
JSON-RPC error whose message tells the engineer (and the model) what
happened and what to do about it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any, TypeVar

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED, INTERNAL_ERROR, Implementation, InitializeResult

from . import SIGNUP_URL, __version__

log = logging.getLogger("fast_engineer_mcp")

T = TypeVar("T")

USER_AGENT = f"fast-engineer-mcp/{__version__}"
# Distinct from the SDK's own codes (CONNECTION_CLOSED is -32000) so reconnection logic never
# mistakes a translated HTTP error for a dropped connection.
HTTP_ERROR_CODE = -32001
UNREACHABLE_CODE = -32002

TransportFactory = Callable[[], httpx2.AsyncBaseTransport]


def _request_id(request: httpx2.Request) -> Any:
    try:
        body = json.loads(request.content)
    except (ValueError, TypeError):
        return None
    return body.get("id") if isinstance(body, dict) else None


def _is_jsonrpc_error(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("jsonrpc") == "2.0" and isinstance(payload.get("error"), dict)


def describe_http_error(status: int, payload: Any, headers: httpx2.Headers, *, has_key: bool) -> tuple[str, dict]:
    """A message for the model and structured data for the client, from an HTTP error reply."""
    body = payload if isinstance(payload, dict) else {}
    detail = body.get("detail") or body.get("error_description") or body.get("error") or ""
    if not detail and isinstance(payload, str):
        detail = payload[:200]
    data: dict[str, Any] = {"http_status": status}
    if detail:
        data["detail"] = detail

    if status == 429:
        retry = body.get("retry_after_seconds") or headers.get("retry-after")
        if retry is not None:
            data["retry_after_seconds"] = retry
        message = "fast.engineer rate limit reached"
        if detail:
            message += f": {detail}"
        if retry is not None:
            message += f" Retry in {retry} s."
        if not has_key:
            message += f" A free key from {SIGNUP_URL} raises the limit; run `fast-engineer-mcp login` to use one."
        data["kind"] = "rate_limited"
    elif status == 503:
        message = f"fast.engineer is unavailable: {detail or 'service temporarily disabled'}"
        data["kind"] = "unavailable"
    elif status == 401:
        message = "fast.engineer rejected the credentials (HTTP 401). Run `fast-engineer-mcp login` with a current key."
        data["kind"] = "unauthorized"
    elif status == 403:
        message = f"fast.engineer refused the call (HTTP 403): {detail or 'forbidden'}"
        data["kind"] = "forbidden"
    elif status == 413:
        message = "fast.engineer refused the request: it is larger than 1 MB."
        data["kind"] = "too_large"
    else:
        message = f"fast.engineer returned HTTP {status}" + (f": {detail}" if detail else ".")
        data["kind"] = "http_error"
    return message, data


class HttpErrorTranslator(httpx2.AsyncBaseTransport):
    """Rewrites fast.engineer's plain-JSON HTTP errors into JSON-RPC errors the SDK surfaces verbatim."""

    def __init__(self, inner: httpx2.AsyncBaseTransport, *, has_key: bool):
        self._inner = inner
        self._has_key = has_key

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._inner.handle_async_request(request)
        if response.status_code < 400:
            return response
        try:
            raw = await response.aread()
        finally:
            await response.aclose()
        content_type = response.headers.get("content-type", "").lower()
        payload: Any = None
        if content_type.startswith("application/json"):
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
        if _is_jsonrpc_error(payload):
            # Already a proper JSON-RPC error (e.g. unknown tool at 400/404): hand it through untouched.
            return httpx2.Response(
                response.status_code, headers=response.headers, content=raw, request=request,
            )
        if payload is None and raw:
            payload = raw.decode("utf-8", "replace")
        message, data = describe_http_error(response.status_code, payload, response.headers, has_key=self._has_key)
        log.warning("fast.engineer HTTP %s: %s", response.status_code, message)
        body = {"jsonrpc": "2.0", "id": _request_id(request),
                "error": {"code": HTTP_ERROR_CODE, "message": message, "data": data}}
        return httpx2.Response(
            response.status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode("utf-8"),
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


class Remote:
    """One lazily-opened client session to the hosted endpoint, rebuilt when the connection drops."""

    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        *,
        read_timeout: float = 300.0,
        connect_timeout: float = 30.0,
        transport_factory: TransportFactory | None = None,
    ):
        self.url = url
        self.api_key = api_key
        self._timeout = httpx2.Timeout(connect_timeout, read=read_timeout)
        self._transport_factory = transport_factory or httpx2.AsyncHTTPTransport
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self.init_result: InitializeResult | None = None

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def __aenter__(self) -> Remote:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _open(self) -> None:
        stack = AsyncExitStack()
        try:
            client = httpx2.AsyncClient(
                headers=self._headers(),
                timeout=self._timeout,
                transport=HttpErrorTranslator(self._transport_factory(), has_key=self.has_key),
            )
            await stack.enter_async_context(client)
            read, write = await stack.enter_async_context(
                streamable_http_client(self.url, http_client=client, terminate_on_close=False)
            )
            session = await stack.enter_async_context(
                ClientSession(read, write, client_info=Implementation(name="fast-engineer-mcp", version=__version__))
            )
            self.init_result = await session.initialize()
        except BaseException as exc:
            # A transport failure (refused connection, DNS) kills the SDK's task group, which cancels
            # this task; the real cause only surfaces when the context stack is unwound.
            cause = await _close_capturing(stack)
            if isinstance(exc, anyio.get_cancelled_exc_class()) and cause is not None:
                raise cause from None
            raise
        self._stack, self._session = stack, session
        log.info("Connected to %s (%s)", self.url, "with key" if self.has_key else "anonymous")

    async def ensure(self) -> ClientSession:
        """The live session, opening it on first use."""
        if self._session is None:
            try:
                await self._open()
            except MCPError:
                raise
            except Exception as exc:  # DNS, refused connection, TLS, bad URL...
                raise MCPError(
                    code=UNREACHABLE_CODE,
                    message=f"Cannot reach fast.engineer at {self.url}: {_describe_exception(exc)}",
                ) from exc
        assert self._session is not None
        return self._session

    async def close(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            await _quiet_close(stack)

    async def call(self, fn: Callable[[ClientSession], Awaitable[T]]) -> T:
        """Run ``fn`` against the session; on a dropped connection, reconnect once and retry."""
        session = await self.ensure()
        try:
            return await fn(session)
        except MCPError as exc:
            if exc.code != CONNECTION_CLOSED:
                raise
            log.warning("Connection to %s dropped; reconnecting", self.url)
        await self.close()
        session = await self.ensure()
        try:
            return await fn(session)
        except MCPError as exc:
            if exc.code != CONNECTION_CLOSED:
                raise
            await self.close()
            raise MCPError(
                code=INTERNAL_ERROR, message=f"Lost the connection to fast.engineer at {self.url}; try again.",
            ) from exc

    async def instructions(self) -> str | None:
        """The hosted server's instructions, or None when it cannot be reached right now."""
        try:
            await self.ensure()
        except MCPError as exc:
            log.warning("%s", exc.message)
            return None
        return self.init_result.instructions if self.init_result else None


def _describe_exception(exc: BaseException) -> str:
    inner = exc
    # anyio wraps concurrent failures in (possibly nested) groups; show the first real cause.
    while getattr(inner, "exceptions", None):
        inner = inner.exceptions[0]
    text = str(inner).strip()
    return f"{type(inner).__name__}: {text}" if text else type(inner).__name__


async def _close_capturing(stack: AsyncExitStack) -> BaseException | None:
    """Close the stack; return (instead of raising) whatever the transport's task group reports."""
    try:
        await stack.aclose()
    except Exception as exc:
        return exc
    return None


async def _quiet_close(stack: AsyncExitStack) -> None:
    cause = await _close_capturing(stack)
    if cause is not None:
        log.debug("Ignored while closing the remote session: %s", _describe_exception(cause))
