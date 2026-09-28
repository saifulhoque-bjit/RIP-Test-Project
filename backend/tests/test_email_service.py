"""Unit tests for EmailService (app.services.email_service).

The shared Celery dispatch (``send_notification_email.delay``) is patched at
its import site in this module — no real Celery broker is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
import uuid

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER
from app.models.postgres.tenant_model import Tenant
from app.services.email_service import EmailService


def _make_tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "Acme"
    t.contact_email = "jane@acme.test"
    t.status = "active"
    t.created_at = datetime.now(tz=UTC)
    t.updated_at = datetime.now(tz=UTC)
    return t


class TestSendTenantCreated:
    def test_dispatches_with_tenant_name_and_contact_email_in_body(self) -> None:
        tenant = _make_tenant()

        with patch("app.services.email_service.send_notification_email.delay") as mock_delay:
            EmailService.send_tenant_created(tenant)

        mock_delay.assert_called_once_with(
            recipient="jane@acme.test",
            subject="Your organization 'Acme' is now on RIP",
            body=mock_delay.call_args.kwargs["body"],
        )
        body = mock_delay.call_args.kwargs["body"]
        assert "Acme" in body
        assert "jane@acme.test" in body


class TestSendAccountStatusChanged:
    def test_deactivated_dispatches_no_cta_and_no_greeting_name(self) -> None:
        with patch("app.services.email_service.send_notification_email.delay") as mock_delay:
            EmailService.send_account_status_changed(
                recipient="alice@example.com", name=None, is_active=False
            )

        mock_delay.assert_called_once_with(
            recipient="alice@example.com",
            subject="Your RIP account has been deactivated",
            body=mock_delay.call_args.kwargs["body"],
        )
        body = mock_delay.call_args.kwargs["body"]
        assert "Hello," in body
        assert "deactivated" in body
        assert "Go to RIP" not in body

    def test_reactivated_dispatches_with_cta_and_greeting_name(self) -> None:
        with patch("app.services.email_service.send_notification_email.delay") as mock_delay:
            EmailService.send_account_status_changed(
                recipient="alice@example.com", name="Alice", is_active=True
            )

        mock_delay.assert_called_once_with(
            recipient="alice@example.com",
            subject="Your RIP account has been reactivated",
            body=mock_delay.call_args.kwargs["body"],
        )
        body = mock_delay.call_args.kwargs["body"]
        assert "Hi Alice," in body
        assert "reactivated" in body
        assert "Go to RIP" in body


class TestSendInvitation:
    def test_dispatches_with_invite_link_and_greeting(self) -> None:
        with patch("app.services.email_service.send_notification_email.delay") as mock_delay:
            EmailService.send_invitation(
                recipient="invitee@example.com",
                tenant_name="Acme",
                invite_link="https://rip.test/invitations/accept?token=raw-token-123",
                role_name=ROLE_MEMBER,
                invitee_name="Invitee Name",
            )

        mock_delay.assert_called_once_with(
            recipient="invitee@example.com",
            subject="You're invited to join Acme on RIP",
            body=mock_delay.call_args.kwargs["body"],
        )
        body = mock_delay.call_args.kwargs["body"]
        assert "raw-token-123" in body
        assert "Hi Invitee Name," in body
        assert "join" in body  # default verb for a non-admin role

    def test_admin_role_uses_administer_verb(self) -> None:
        with patch("app.services.email_service.send_notification_email.delay") as mock_delay:
            EmailService.send_invitation(
                recipient="invitee@example.com",
                tenant_name="Acme",
                invite_link="https://rip.test/invitations/accept?token=tok",
                role_name=ROLE_ADMIN,
            )

        body = mock_delay.call_args.kwargs["body"]
        assert "administer" in body

    def test_missing_invitee_name_falls_back_to_generic_greeting(self) -> None:
        with patch("app.services.email_service.send_notification_email.delay") as mock_delay:
            EmailService.send_invitation(
                recipient="invitee@example.com",
                tenant_name="Acme",
                invite_link="https://rip.test/invitations/accept?token=tok",
                role_name=ROLE_MEMBER,
            )

        body = mock_delay.call_args.kwargs["body"]
        assert "Hello," in body
