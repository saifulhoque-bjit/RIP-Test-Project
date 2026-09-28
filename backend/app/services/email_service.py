"""Outbound transactional email templates + dispatch.

Owns the RIP-branded HTML email chrome and the per-email-type subject/body
builders. Callers hand over the business data (a ``Tenant``, an invite link,
a role) and this service builds the content and fires the existing
``send_notification_email`` Celery task — centralizing this avoids
re-duplicating the HTML wrapper for every new transactional email type.

Stateless by design (no ``UnitOfWork``/repository dependency) — every method
is a ``@staticmethod``, called directly as ``EmailService.send_x(...)``.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.constants import ROLE_ADMIN
from app.models.postgres.tenant_model import Tenant
from app.workers.tasks import send_notification_email

_INVITE_VERB_BY_ROLE = {ROLE_ADMIN: "administer"}


def _render_email_shell(body_html: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="en">
  <body style="margin:0;padding:0;background-color:#f4f5f7;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5f7;padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background-color:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">
            <tr>
              <td style="background-color:#111827;padding:24px 32px;">
                <span style="color:#ffffff;font-size:20px;font-weight:600;letter-spacing:0.5px;">RIP</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;">
{body_html}
              </td>
            </tr>
            <tr>
              <td style="padding:20px 32px;background-color:#f9fafb;border-top:1px solid #e5e7eb;">
                <p style="margin:0;font-size:12px;color:#9ca3af;">
                  This is an automated message from RIP. Please do not reply to this email.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _cta_button(*, href: str, label: str) -> str:
    return f"""\
                <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                  <tr>
                    <td style="border-radius:6px;background-color:#2563eb;">
                      <a href="{href}" style="display:inline-block;padding:12px 28px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:6px;">
                        {label}
                      </a>
                    </td>
                  </tr>
                </table>"""


class EmailService:
    """Builds and dispatches RIP's transactional emails."""

    @staticmethod
    def send_tenant_created(tenant: Tenant) -> None:
        """Notify a newly created tenant's contact email that it now exists."""
        body_html = f"""\
                <p style="margin:0 0 16px;font-size:16px;color:#111827;">Hello,</p>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#374151;">
                  Your organization <strong>{tenant.name}</strong> has been created on
                  RIP (Requirement Intelligence Platform), registered under
                  <strong>{tenant.contact_email}</strong>.
                </p>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#374151;">
                  A separate invitation email will follow so your Client Admin can
                  sign in and start managing projects.
                </p>
{_cta_button(href=settings.FRONTEND_BASE_URL, label="Go to RIP")}
                <p style="margin:0;font-size:13px;color:#9ca3af;">
                  If you weren't expecting this email, you can safely ignore it.
                </p>"""
        send_notification_email.delay(
            recipient=tenant.contact_email,
            subject=f"Your organization '{tenant.name}' is now on RIP",
            body=_render_email_shell(body_html),
        )

    @staticmethod
    def send_account_status_changed(
        *,
        recipient: str,
        name: str | None,
        is_active: bool,
    ) -> None:
        """Notify a user that an admin has activated or deactivated their account.

        Sent for both directions (deactivate and reactivate) so the account
        holder always has a record of the change, even though it's an
        admin/Client-Admin-initiated action they didn't trigger themselves —
        the same "you should know when your access changes" principle as a
        password-reset or login-from-new-device notice.
        """
        greeting = f"Hi {name}," if name else "Hello,"
        if is_active:
            body_html = f"""\
                <p style="margin:0 0 16px;font-size:16px;color:#111827;">{greeting}</p>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#374151;">
                  Your RIP (Requirement Intelligence Platform) account has been
                  <strong>reactivated</strong>. You can sign in and resume your work.
                </p>
{_cta_button(href=settings.FRONTEND_BASE_URL, label="Go to RIP")}
                <p style="margin:0;font-size:13px;color:#9ca3af;">
                  If you weren't expecting this change, please contact your administrator.
                </p>"""
            subject = "Your RIP account has been reactivated"
        else:
            body_html = f"""\
                <p style="margin:0 0 16px;font-size:16px;color:#111827;">{greeting}</p>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#374151;">
                  Your RIP (Requirement Intelligence Platform) account has been
                  <strong>deactivated</strong> by an administrator. You will not be able
                  to sign in until it is reactivated.
                </p>
                <p style="margin:0;font-size:13px;color:#9ca3af;">
                  If you believe this was a mistake, please contact your administrator.
                </p>"""
            subject = "Your RIP account has been deactivated"
        send_notification_email.delay(
            recipient=recipient,
            subject=subject,
            body=_render_email_shell(body_html),
        )

    @staticmethod
    def send_invitation(
        *,
        recipient: str,
        tenant_name: str,
        invite_link: str,
        role_name: str,
        invitee_name: str | None = None,
    ) -> None:
        """Notify an invitee with their invite link."""
        verb = _INVITE_VERB_BY_ROLE.get(role_name, "join")
        greeting = f"Hi {invitee_name}," if invitee_name else "Hello,"
        body_html = f"""\
                <p style="margin:0 0 16px;font-size:16px;color:#111827;">{greeting}</p>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#374151;">
                  You've been invited to {verb} <strong>{tenant_name}</strong> on RIP.
                  Click the button below to accept your invitation and set up your account.
                </p>
{_cta_button(href=invite_link, label="Accept Invitation")}
                <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">
                  Or copy and paste this link into your browser:
                </p>
                <p style="margin:0 0 24px;font-size:13px;word-break:break-all;">
                  <a href="{invite_link}" style="color:#2563eb;">{invite_link}</a>
                </p>
                <p style="margin:0;font-size:13px;color:#9ca3af;">
                  This invitation link expires in {settings.INVITATION_EXPIRY_DAYS} day(s).
                  If you weren't expecting this invitation, you can safely ignore this email.
                </p>"""
        send_notification_email.delay(
            recipient=recipient,
            subject=f"You're invited to join {tenant_name} on RIP",
            body=_render_email_shell(body_html),
        )
