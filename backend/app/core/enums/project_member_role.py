"""Role a user holds within a specific project (see ProjectMember)."""

from __future__ import annotations

from enum import Enum


class ProjectMemberRole(str, Enum):
    MEMBER = "member"


# Human-readable labels for API responses.
PROJECT_MEMBER_ROLE_DISPLAY_LABELS: dict[str, str] = {
    ProjectMemberRole.MEMBER.value: "Member",
}
