"""HTTP client for the TAP (Test Automation Platform) REST API.

TAP auth: the API key and app-client id are sent under two spellings each
(``X-API-Key``/``API-Key`` and ``X-App-Client-Id``/``Client-Id``) because TAP
requires different ones on different endpoints. See ``_auth_headers``.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.exceptions import TapClientError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_TIMEOUT_SECONDS = 30


class TapClient:
    def __init__(self, base_url: str, auth_config: dict | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_config = auth_config or {}
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self._auth_headers(),
        }

    # ── Auth ─────────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """Return TAP's auth headers, in **both** spellings TAP accepts.

        TAP is not consistent across its own endpoints, and each rejects the
        other's spelling with a 422 naming the fields it wanted:

        - ``POST /api/v1/sync/requirements/notify`` → ``X-API-Key`` /
          ``X-App-Client-Id``
        - ``GET  /api/v1/app-clients/verify``       → ``API-Key`` /
          ``Client-Id``

        Both observed against the live dev deployment. Picking either one
        alone breaks the other call, so send both — the endpoint that does not
        recognise a header simply ignores it. Do not "tidy" this down to one
        pair; that regression has already been shipped twice.

        Sourced from ``auth_config`` (``{"api_key": ..., "app_client_id": ...}``),
        which the caller fills from the project's own integration row. Empty
        values are omitted rather than sent blank.
        """
        headers: dict[str, str] = {}
        api_key = self._auth_config.get("api_key")
        client_id = self._auth_config.get("app_client_id")
        if api_key:
            headers["X-API-Key"] = api_key
            headers["API-Key"] = api_key
        if client_id:
            headers["X-App-Client-Id"] = client_id
            headers["Client-Id"] = client_id
        return headers

    # ── Public methods ─────────────────────────────────────────────────────

    async def notify_sync_ready(self, *, project_name: str, project_id: str, sync_id: str) -> dict:
        """Ping TAP that a new requirements batch is staged and ready to pull.

        RIP does not push the hierarchy inline — it stages the payload and only
        sends this lightweight notification to
        ``POST /api/v1/sync/requirements/notify``, with the exact body TAP's
        team confirmed: ``{"project_name", "data_type", "project_id",
        "sync_id"}``. TAP is expected to call RIP's ``GET .../sync/{sync_id}/data``
        (guarded by the shared secret) at its own pace to fetch the actual data —
        it derives that pull request itself from ``project_id`` + ``sync_id``.

        TAP responds 202 with ``{"success": true, "data": {"status":
        "NOTIFICATION_RECEIVED", ...}}`` — anything else is treated as a
        failed notification.
        """
        response = await self._request(
            "POST",
            "/api/v1/sync/requirements/notify",
            json={
                "project_name": project_name,
                "data_type": "requirement",
                "project_id": project_id,
                "sync_id": sync_id,
            },
        )
        data = response.get("data") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict)
            or not response.get("success")
            or not isinstance(data, dict)
            or data.get("status") != "NOTIFICATION_RECEIVED"
        ):
            raise TapClientError(f"TAP notify returned an unexpected response: {response!r}")
        return data

    async def verify_credentials(self, app_client_name: str) -> dict:
        """Call ``GET /api/v1/app-clients/verify`` to validate the credentials
        held by this client instance.

        TAP responds with ``{"success": true, "data": {"verified": true, ...}}``.
        Raises :class:`TapClientError` if credentials are rejected or TAP is
        unreachable.
        """
        response = await self._request(
            "GET",
            "/api/v1/app-clients/verify",
            params={"app_client_name": app_client_name},
        )
        if not isinstance(response, dict) or not response.get("success"):
            raise TapClientError(f"TAP credential verification failed: {response!r}")
        data = response.get("data") or {}
        if not data.get("verified"):
            raise TapClientError("TAP reported credentials as unverified.")
        return data

    # ── Internal request with retry ────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | list | None = None,
        params: dict | None = None,
    ) -> dict | list:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    resp = await client.request(
                        method, url, headers=self._headers, json=json, params=params
                    )

                if resp.status_code == 204:
                    return {}

                if resp.is_success:
                    return resp.json()

                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "TAP API transient error: status=%s attempt=%s path=%s",
                        resp.status_code,
                        attempt + 1,
                        path,
                    )
                    last_exc = TapClientError(
                        f"TAP API returned {resp.status_code}: {resp.text[:500]}"
                    )
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                        continue
                    raise last_exc

                raise TapClientError(f"TAP API error {resp.status_code}: {resp.text[:500]}")

            except httpx.TimeoutException:
                logger.warning("TAP API timeout: attempt=%s path=%s", attempt + 1, path)
                last_exc = TapClientError(f"TAP API timeout on {method} {path}")
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue

            except httpx.ConnectError as exc:
                logger.warning("TAP API connection error: attempt=%s path=%s", attempt + 1, path)
                last_exc = TapClientError(f"TAP connection failed: {exc}")
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue

        raise last_exc or TapClientError("TAP API request failed after retries")
