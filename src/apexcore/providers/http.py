"""Shared HTTP plumbing for adapters: client creation, retries, error mapping, SSE."""

from __future__ import annotations

import contextlib
import json
import random
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from apexcore import __version__
from apexcore.errors import (
    AuthenticationError,
    BadRequestError,
    ModelNotFoundError,
    NetworkError,
    ProviderError,
    ProviderResponseError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
)
from apexcore.redaction import GLOBAL_REDACTOR, Redactor

USER_AGENT = f"apex-core-cli/{__version__}"
MAX_RETRY_AFTER = 60.0


def make_client(*, base_url: str, headers: dict[str, str], timeout: float) -> httpx.Client:
    """Create the HTTP client used by an adapter.

    Tests replace this function to inject :class:`httpx.MockTransport`.
    ``trust_env`` keeps the standard proxy and CA-bundle environment variables
    (``HTTPS_PROXY``, ``SSL_CERT_FILE``...) working.
    """
    return httpx.Client(
        base_url=base_url,
        headers={"User-Agent": USER_AGENT, **headers},
        timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
        follow_redirects=False,
        trust_env=True,
    )


def _error_detail(response: httpx.Response) -> tuple[str, str]:
    """Extract (message, code) from common JSON error envelopes."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500].strip(), ""
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            message = str(err.get("message") or err.get("detail") or "")
            code = str(err.get("code") or err.get("type") or err.get("status") or "")
            return message[:500], code
        if isinstance(err, str):
            return err[:500], ""
    return str(body)[:500], ""


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class HTTPProviderMixin:
    """Common behaviour for HTTP adapters. Subclasses set the attributes below."""

    name: str
    api_key_env: str | None
    base_url: str
    max_retries: int
    _client: httpx.Client
    _redactor: Redactor
    _sleep: Callable[[float], None] = staticmethod(time.sleep)

    def _init_http(
        self,
        *,
        name: str,
        base_url: str,
        headers: dict[str, str],
        timeout: float,
        max_retries: int,
        api_key_env: str | None,
        redactor: Redactor | None,
    ) -> None:
        self.name = name
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.max_retries = max_retries
        self._redactor = redactor or GLOBAL_REDACTOR
        self._client_args = {"base_url": base_url, "headers": headers, "timeout": timeout}
        self._client = make_client(**self._client_args)

    def close(self) -> None:
        self._client.close()

    def cancel(self) -> None:
        """Abort in-flight requests by closing their connections, and start a
        fresh client so the provider stays usable for the next turn."""
        old = self._client
        self._client = make_client(**self._client_args)
        with contextlib.suppress(Exception):
            old.close()

    # --- Error mapping ---------------------------------------------------

    def _clean(self, text: str) -> str:
        return self._redactor.redact_aggressive(text)

    def map_http_error(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        message, code = _error_detail(response)
        detail = self._clean(message) or response.reason_phrase or "no details"
        prefix = f"{self.name}: HTTP {status}"
        lowered = f"{detail} {code}".lower()
        key_hint = (
            f"Check the {self.api_key_env} key (`apex doctor` shows where it was found)."
            if self.api_key_env
            else "Check the provider's authentication settings."
        )
        if status == 401:
            return AuthenticationError(f"{prefix}: authentication failed: {detail}", hint=key_hint)
        if status == 403:
            return AuthenticationError(
                f"{prefix}: access denied: {detail}",
                hint="The key may lack access to this model, project, or region. " + key_hint,
            )
        if status == 404:
            if "model" in lowered:
                return ModelNotFoundError(
                    f"{prefix}: model not found: {detail}",
                    hint=f"Run `apex models --provider {self.name}` to see available models.",
                )
            return ProviderError(
                f"{prefix}: endpoint not found: {detail}",
                hint=f"Check base_url ({self.base_url}) for provider {self.name!r}.",
            )
        if status == 402 or "insufficient_quota" in lowered or "credit balance" in lowered:
            return QuotaExceededError(
                f"{prefix}: quota or credit exhausted: {detail}",
                hint="Check the billing settings for this account.",
            )
        if status == 429:
            return RateLimitError(
                f"{prefix}: rate limited: {detail}",
                hint="Wait and retry, or lower your request rate.",
                retry_after=_retry_after(response),
            )
        if status in (400, 422):
            if "model" in lowered and any(
                word in lowered
                for word in ("not found", "does not exist", "invalid model", "unknown")
            ):
                return ModelNotFoundError(
                    f"{prefix}: {detail}",
                    hint=f"Run `apex models --provider {self.name}` to see available models.",
                )
            hint = None
            if "tool" in lowered or "function" in lowered:
                hint = "This model may not support tool calling; try --no-tools."
            return BadRequestError(f"{prefix}: request rejected: {detail}", hint=hint)
        if status == 408 or status >= 500:
            return ProviderUnavailableError(
                f"{prefix}: provider error: {detail}",
                hint="The service may be overloaded or down; try again shortly.",
            )
        return ProviderError(f"{prefix}: {detail}")

    def map_transport_error(self, exc: httpx.HTTPError) -> ProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return NetworkError(
                f"{self.name}: request timed out ({type(exc).__name__}).",
                hint="Increase `timeout` for this provider or check your connection.",
            )
        if isinstance(exc, httpx.ConnectError):
            return NetworkError(
                f"{self.name}: could not connect to {self.base_url}: {self._clean(str(exc))}",
                hint="Check your network, proxy settings, and the provider's base_url"
                + (" (is the local server running?)" if "localhost" in self.base_url else "."),
            )
        return NetworkError(f"{self.name}: network error: {self._clean(str(exc))}")

    # --- Requests --------------------------------------------------------

    def _backoff(self, attempt: int, error: ProviderError) -> float | None:
        """Seconds to wait before retrying, or None if we should not retry."""
        if not error.retryable or attempt >= self.max_retries:
            return None
        if isinstance(error, RateLimitError) and error.retry_after is not None:
            if error.retry_after > MAX_RETRY_AFTER:
                return None
            return error.retry_after
        return min(2.0**attempt, 16.0) + random.uniform(0, 0.5)

    @contextlib.contextmanager
    def request(
        self, method: str, path: str, *, json_body: Any = None, stream: bool = False
    ) -> Iterator[httpx.Response]:
        """Send a request with retries; yields a successful (2xx) response.

        Retries happen only before any response body has been handed to the
        caller, so streamed output is never duplicated.
        """
        attempt = 0
        while True:
            try:
                request = self._client.build_request(method, path, json=json_body)
                response = self._client.send(request, stream=stream)
            except httpx.HTTPError as exc:
                error = self.map_transport_error(exc)
            else:
                if response.status_code < 400:
                    try:
                        yield response
                    finally:
                        response.close()
                    return
                try:
                    response.read()
                    error = self.map_http_error(response)
                finally:
                    response.close()
            delay = self._backoff(attempt, error)
            if delay is None:
                raise error
            attempt += 1
            self._sleep(delay)

    def get_json(self, path: str) -> Any:
        with self.request("GET", path) as response:
            return self.parse_json(response)

    def parse_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                f"{self.name}: response was not valid JSON: {self._clean(response.text[:200])}"
            ) from exc


def iter_sse(response: httpx.Response, provider: HTTPProviderMixin) -> Iterator[tuple[str, str]]:
    """Yield ``(event, data)`` pairs from a server-sent-events stream."""
    event = ""
    data_lines: list[str] = []
    try:
        for line in response.iter_lines():
            if not line:
                if data_lines:
                    yield event, "\n".join(data_lines)
                event, data_lines = "", []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event = value
            elif field == "data":
                data_lines.append(value)
        if data_lines:
            yield event, "\n".join(data_lines)
    except httpx.HTTPError as exc:
        raise provider.map_transport_error(exc) from exc


def load_event_json(data: str, provider: HTTPProviderMixin) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise ProviderResponseError(
            f"{provider.name}: malformed stream event: {provider._clean(data[:200])}"
        ) from exc
    if not isinstance(value, dict):
        raise ProviderResponseError(f"{provider.name}: unexpected stream event shape")
    return value
