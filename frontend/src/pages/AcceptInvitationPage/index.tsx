// Accept Invitation Page - public route (/invitations/accept?token=...)
// Resolves the invitation token to an email/tenant, then lets the invited
// user set a password to activate their account.
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "@/lib/toast";
import type { FetchBaseQueryError } from "@reduxjs/toolkit/query/react";
import { validators } from "@/constants/validators";
import { useAppDispatch } from "@/store/hooks";
import { setUnauthenticated } from "@/store/slices/authSlice";
import {
  useGetInvitationDetailsQuery,
  useAcceptInvitationMutation,
} from "@/services/api/modules/invitations";
import { useLogoutMutation } from "@/services/api/modules/auth";
import { baseApi } from "@/services/api/baseApi";
import Button from "@/components/common/Button/Button";
import Input from "@/components/common/Input";
import { EyeIcon } from "@/assets/icons/EyeIcon";
import { EyeOffIcon } from "@/assets/icons/EyeOffIcon";
import { CheckCircleIcon } from "@/assets/icons/CheckCircleIcon";
import { SpinnerIcon } from "@/assets/icons/SpinnerIcon";
import PublicPageLayout from "@/components/layout/PublicPageLayout";

// ── Inline field validation ───────────────────────────────────────────────────

interface FieldErrors {
  password: string;
  confirmPassword: string;
}

function validate(password: string, confirmPassword: string): FieldErrors {
  const errors: FieldErrors = { password: "", confirmPassword: "" };

  const passwordValidation = validators.passwordStrong(password);
  if (!passwordValidation.isValid) {
    errors.password = passwordValidation.error || "";
  }

  const matchValidation = validators.match(
    password,
    confirmPassword,
    "Passwords",
  );
  if (!matchValidation.isValid) {
    errors.confirmPassword = matchValidation.error || "";
  } else if (!confirmPassword) {
    errors.confirmPassword = "Please confirm your password.";
  }

  return errors;
}

function extractErrorMessage(err: unknown, fallback: string): string {
  const fetchErr = err as FetchBaseQueryError | undefined;
  if (fetchErr && "data" in fetchErr && fetchErr.data) {
    const data = fetchErr.data as { message?: string; error?: string };
    return data.message || data.error || fallback;
  }
  return fallback;
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function AcceptInvitationPage() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const {
    data: invitationData,
    isLoading: isLoadingInvitation,
    isError: isInvitationError,
    error: invitationError,
  } = useGetInvitationDetailsQuery(token, { skip: !token });

  const [acceptInvitation, { isLoading: isAccepting }] =
    useAcceptInvitationMutation();
  const [logout, { isLoading: isLoggingOut }] = useLogoutMutation();

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({
    password: "",
    confirmPassword: "",
  });
  const [apiError, setApiError] = useState("");
  const [accepted, setAccepted] = useState(false);

  function handlePasswordChange(e: React.ChangeEvent<HTMLInputElement>) {
    setPassword(e.target.value);
    if (fieldErrors.password)
      setFieldErrors((prev) => ({ ...prev, password: "" }));
    if (apiError) setApiError("");
  }

  function handleConfirmPasswordChange(e: React.ChangeEvent<HTMLInputElement>) {
    setConfirmPassword(e.target.value);
    if (fieldErrors.confirmPassword)
      setFieldErrors((prev) => ({ ...prev, confirmPassword: "" }));
    if (apiError) setApiError("");
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setApiError("");

    const errors = validate(password, confirmPassword);
    if (errors.password || errors.confirmPassword) {
      setFieldErrors(errors);
      return;
    }

    try {
      await acceptInvitation({
        token,
        body: { password, confirm_password: confirmPassword },
      }).unwrap();

      setAccepted(true);
      toast.success("Your account is ready. You can now sign in.");
    } catch (err: unknown) {
      setApiError(
        extractErrorMessage(
          err,
          "Could not accept this invitation. Please try again.",
        ),
      );
    }
  }

  async function handleContinueToLogin() {
    try {
      await logout().unwrap();
    } catch {
      // The local session still needs to be cleared so the invitee can sign in.
    } finally {
      dispatch(setUnauthenticated());
      dispatch(baseApi.util.resetApiState());
      navigate("/login", { replace: true });
    }
  }

  const invitation = invitationData?.data;
  const invitationErrorMessage = extractErrorMessage(
    invitationError,
    "This invitation link is invalid or has expired.",
  );

  return (
    <PublicPageLayout>
      {/* Right Panel - Form Card */}
      <div className="w-[420px] max-w-[90%] rounded-lg border border-border bg-surface p-11 shadow-e1">
        {!token ? (
          <div>
            <h2 className="text-[22px] font-semibold">
              Invalid invitation link
            </h2>
            <p className="mt-2 text-[13px] text-sec">
              This invitation link is missing a token. Please use the link from
              your invitation email, or contact your administrator for a new
              one.
            </p>
          </div>
        ) : isLoadingInvitation ? (
          <div className="flex flex-col items-center gap-3 py-6">
            <SpinnerIcon className="h-6 w-6 animate-spin text-accent" />
            <p className="text-[13px] text-sec">Verifying your invitation...</p>
          </div>
        ) : isInvitationError || !invitation ? (
          <div>
            <h2 className="text-[22px] font-semibold">
              Invitation not available
            </h2>
            <p className="mt-2 text-[13px] text-sec">
              {invitationErrorMessage}
            </p>
          </div>
        ) : accepted ? (
          <div className="flex flex-col items-center gap-3 py-4 text-center">
            <CheckCircleIcon className="h-10 w-10 text-[var(--success)]" />
            <h2 className="text-[22px] font-semibold">Account activated</h2>
            <p className="text-[13px] text-sec">
              Your password has been set. You can now sign in to{" "}
              <b className="font-semibold text-primary">
                {invitation.tenant_name}
              </b>
              .
            </p>
            <Button
              className="mt-2 w-full"
              size="md"
              loading={isLoggingOut}
              loadingText="Opening sign in..."
              onClick={handleContinueToLogin}
            >
              Go to sign in
            </Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <h2 className="text-[22px] font-semibold">
              Accept your invitation
            </h2>
            <p className="mb-6 text-[13px] text-sec">
              You've been invited to join{" "}
              <b className="font-semibold text-primary">
                {invitation.tenant_name}
              </b>
              . Set a password to activate your account.
            </p>

            {apiError && (
              <div className="min-h-[42px] mb-4 p-3 bg-[var(--error)]/10 border border-[var(--error)]/20 rounded-lg text-[var(--error)] text-sm">
                {apiError}
              </div>
            )}

            <div className="mb-4">
              <Input
                id="invitation-email"
                type="email"
                label="Email"
                value={invitation.email}
                readOnly
                disabled
                size="md"
              />
            </div>

            <div className="mb-4">
              <Input
                id="invitation-password"
                name="password"
                label="Password"
                required
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={handlePasswordChange}
                error={!!fieldErrors.password}
                errorMessage={fieldErrors.password}
                className="bg-white"
                endIcon={
                  showPassword ? (
                    <EyeOffIcon className="h-4 w-4" />
                  ) : (
                    <EyeIcon className="h-4 w-4" />
                  )
                }
                endIconAriaLabel={
                  showPassword ? "Hide password" : "Show password"
                }
                onEndIconClick={() => setShowPassword((prev) => !prev)}
                size="md"
              />
            </div>

            <div className="mb-6">
              <Input
                id="invitation-confirm-password"
                name="confirm_password"
                label="Confirm password"
                required
                type={showConfirmPassword ? "text" : "password"}
                value={confirmPassword}
                onChange={handleConfirmPasswordChange}
                error={!!fieldErrors.confirmPassword}
                errorMessage={fieldErrors.confirmPassword}
                className="bg-white"
                endIcon={
                  showConfirmPassword ? (
                    <EyeOffIcon className="h-4 w-4" />
                  ) : (
                    <EyeIcon className="h-4 w-4" />
                  )
                }
                endIconAriaLabel={
                  showConfirmPassword ? "Hide password" : "Show password"
                }
                onEndIconClick={() => setShowConfirmPassword((prev) => !prev)}
                size="md"
              />
            </div>

            <Button
              type="submit"
              className="w-full"
              size="md"
              loading={isAccepting}
              loadingText="Activating account..."
            >
              Save Password & Activate account
            </Button>
          </form>
        )}
      </div>
    </PublicPageLayout>
  );
}
