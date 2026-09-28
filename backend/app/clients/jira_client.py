"""HTTP client for Jira Cloud REST API v3.

Uses httpx with Basic auth (email + API token).  Includes retry with
exponential backoff on transient errors (429, 5xx, timeouts).
"""

from __future__ import annotations

import asyncio
import base64

import httpx

from app.core.exceptions import JiraClientError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_TIMEOUT_SECONDS = 30


class JiraCloudClient:
    def __init__(self, base_url: str, email: str, api_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Public methods ─────────────────────────────────────────────────────

    async def test_connection(self) -> dict:
        """GET /rest/api/3/myself — validate credentials."""
        return await self._request("GET", "/rest/api/3/myself")

    async def get_project(self, project_key: str) -> dict:
        """GET /rest/api/3/project/{projectKey} — get project details."""
        return await self._request("GET", f"/rest/api/3/project/{project_key}")

    async def get_issue_types(self, project_key: str) -> list[dict]:
        """GET /rest/api/3/project/{projectKey}/issueTypes — get available issue types."""
        project = await self.get_project(project_key)
        return project.get("issueTypes", [])

    async def check_permissions(
        self,
        project_key: str,
        permissions: list[str],
    ) -> dict[str, bool]:
        """GET /rest/api/3/user/permissions — check user permissions in project.

        Args:
            project_key: JIRA project key
            permissions: List of permission keys to check

        Returns:
            Dict with permission names as keys, True/False as values
        """
        params = {
            "projectKey": project_key,
        }

        result = await self._request(
            "GET",
            "/rest/api/3/user/permissions",
            params=params,
        )

        # Result format: {"permissions": {"CREATE_ISSUE": {"havePermission": true}}}
        perms_data = result.get("permissions", {})

        return {perm: perms_data.get(perm, {}).get("havePermission", False) for perm in permissions}

    async def create_issue(self, fields: dict) -> dict:
        """POST /rest/api/3/issue — create an issue and return its key + id."""
        return await self._request("POST", "/rest/api/3/issue", json={"fields": fields})

    async def update_issue(self, issue_id_or_key: str, fields: dict) -> None:
        """PUT /rest/api/3/issue/{id} — update only the supplied fields."""
        await self._request("PUT", f"/rest/api/3/issue/{issue_id_or_key}", json={"fields": fields})

    async def get_issue(self, issue_id_or_key: str, fields: list[str] | None = None) -> dict:
        """GET /rest/api/3/issue/{id}."""
        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        return await self._request("GET", f"/rest/api/3/issue/{issue_id_or_key}", params=params)

    async def transition_issue(self, issue_id_or_key: str, transition_id: str) -> None:
        """POST /rest/api/3/issue/{id}/transitions — move issue to a new status."""
        await self._request(
            "POST",
            f"/rest/api/3/issue/{issue_id_or_key}/transitions",
            json={"transition": {"id": transition_id}},
        )

    async def get_transitions(self, issue_id_or_key: str) -> list[dict]:
        """GET /rest/api/3/issue/{id}/transitions — list available transitions."""
        resp = await self._request("GET", f"/rest/api/3/issue/{issue_id_or_key}/transitions")
        return resp.get("transitions", [])

    async def search_issues(self, jql: str, fields: list[str], max_results: int = 50) -> list[dict]:
        """POST /rest/api/3/search — search issues by JQL."""
        resp = await self._request(
            "POST",
            "/rest/api/3/search",
            json={"jql": jql, "fields": fields, "maxResults": max_results},
        )
        return resp.get("issues", [])

    async def create_field(self, name: str, field_type: str, searcher_key: str) -> dict:
        """POST /rest/api/3/field — create a custom field."""
        return await self._request(
            "POST",
            "/rest/api/3/field",
            json={"name": name, "type": field_type, "searcherKey": searcher_key},
        )

    async def get_fields(self) -> list[dict]:
        """GET /rest/api/3/field — list all fields (system + custom)."""
        return await self._request("GET", "/rest/api/3/field")

    async def create_component(
        self, project_key: str, name: str, description: str | None = None
    ) -> dict:
        """POST /rest/api/3/component — create a project component."""
        body: dict = {"project": project_key, "name": name}
        if description:
            body["description"] = description
        return await self._request("POST", "/rest/api/3/component", json=body)

    async def get_components(self, project_key: str) -> list[dict]:
        """GET /rest/api/3/project/{key}/components."""
        return await self._request("GET", f"/rest/api/3/project/{project_key}/components")

    async def get_issue_types(self, project_key: str) -> list[dict]:
        """GET /rest/api/3/project/{key} — extract issue types."""
        resp = await self._request("GET", f"/rest/api/3/project/{project_key}")
        return resp.get("issueTypes", [])

    async def get_myself(self) -> dict:
        """GET /rest/api/3/myself — return the authenticated user's profile."""
        return await self._request("GET", "/rest/api/3/myself")

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

                # 204 No Content — success with no body
                if resp.status_code == 204:
                    return {}

                # Success
                if resp.is_success:
                    return resp.json()

                # Transient — retry with backoff
                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "Jira API transient error: status=%s attempt=%s path=%s",
                        resp.status_code,
                        attempt + 1,
                        path,
                    )
                    last_exc = JiraClientError(
                        f"Jira API returned {resp.status_code}: {resp.text[:500]}"
                    )
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                        continue
                    raise last_exc

                # Non-transient error — fail immediately
                raise JiraClientError(f"Jira API error {resp.status_code}: {resp.text[:500]}")

            except httpx.TimeoutException:
                logger.warning("Jira API timeout: attempt=%s path=%s", attempt + 1, path)
                last_exc = JiraClientError(f"Jira API timeout on {method} {path}")
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue

            except httpx.ConnectError as exc:
                logger.warning("Jira API connection error: attempt=%s path=%s", attempt + 1, path)
                last_exc = JiraClientError(f"Jira connection failed: {exc}")
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue

            except (httpx.UnsupportedProtocol, httpx.InvalidURL) as exc:
                # Non-transient — a malformed jira_base_url (e.g. missing
                # http(s):// scheme). Retrying won't help, fail immediately.
                raise JiraClientError(f"Invalid Jira base URL: {exc}") from exc

        raise last_exc or JiraClientError("Jira API request failed after retries")
