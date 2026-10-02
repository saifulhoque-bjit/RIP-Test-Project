// Login Page - public route (/login)
// Handles email + password authentication and marks the cookie-backed session as authenticated.
import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { setAuthenticated } from "@/store/slices/authSlice";
import { selectIsAuthenticated } from "@/store/slices/authSlice";
import {
  useLoginMutation,
  useForgotPasswordMutation,
} from "@/services/api/modules/auth";
import { toast } from "@/lib/toast";
import { validators } from "@/constants/validators";
import { getErrorMessage } from "@/utils/getErrorMessage";
import Button from "@/components/common/Button/Button";
import Input from "@/components/common/Input";
import { EyeIcon } from "@/assets/icons/EyeIcon";
import { EyeOffIcon } from "@/assets/icons/EyeOffIcon";
import Modal from "@/components/common/Modal";
import PublicPageLayout from "@/components/layout/PublicPageLayout";

// ── Inline field validation ───────────────────────────────────────────────────

interface FieldErrors {
  email: string;
  password: string;
}

interface SubmittedCredentials {
  email: string;
  password: string;
}

function readSubmittedCredentials(form: HTMLFormElement): SubmittedCredentials {
  const formData = new FormData(form);

  return {
    email: String(formData.get("email") ?? "").trim(),
    password: String(formData.get("password") ?? ""),
  };
}

function validate(email: string, password: string): FieldErrors {
  const errors: FieldErrors = { email: "", password: "" };

  const emailValidation = validators.email(email);
  if (!emailValidation.isValid) {
    errors.email = emailValidation.error || "";
  }

  const passwordValidation = validators.password(password, 8);
  if (!passwordValidation.isValid) {
    errors.password = passwordValidation.error || "";
  }

  return errors;
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * LoginPage — public page that authenticates the user.
 *
 * On success, marks the session authenticated in Redux and navigates to the
 * previously attempted route (or '/' as fallback).
 * JWTs are stored as HttpOnly cookies and are never exposed to client code.
 */
export default function LoginPage() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const isAuthenticated = useAppSelector(selectIsAuthenticated);

  const [login, { isLoading }] = useLoginMutation();
  const [forgotPassword, { isLoading: isSendingReset }] =
    useForgotPasswordMutation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [forgotOpen, setForgotOpen] = useState(false);
  const [resetEmail, setResetEmail] = useState("");
  const [resetEmailError, setResetEmailError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({
    email: "",
    password: "",
  });
  const [apiError, setApiError] = useState("");
  const canSendReset = resetEmail.trim().length > 0;

  function closeForgotPasswordModal() {
    setForgotOpen(false);
    setResetEmail("");
    setResetEmailError("");
  }

  async function sendResetCode() {
    const emailValidation = validators.email(resetEmail);
    if (!emailValidation.isValid) {
      setResetEmailError(emailValidation.error || "");
      return;
    }

    try {
      const trimmedEmail = resetEmail.trim();
      const response = await forgotPassword({
        email: trimmedEmail,
      }).unwrap();
      closeForgotPasswordModal();
      toast.success(
        response.data?.message ||
          "If an account exists for this email, a reset code is on its way.",
      );
      navigate(`/reset-password?email=${encodeURIComponent(trimmedEmail)}`);
    } catch (err: unknown) {
      setResetEmailError(
        getErrorMessage(
          err,
          "Could not send the reset code. Please try again.",
        ),
      );
    }
  }

  // Redirect already-authenticated users away from the login page
  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  function handleEmailChange(e: React.ChangeEvent<HTMLInputElement>) {
    setEmail(e.target.value);
    if (fieldErrors.email) setFieldErrors((prev) => ({ ...prev, email: "" }));
    if (apiError) setApiError("");
  }

  function handlePasswordChange(e: React.ChangeEvent<HTMLInputElement>) {
    setPassword(e.target.value);
    if (fieldErrors.password)
      setFieldErrors((prev) => ({ ...prev, password: "" }));
    if (apiError) setApiError("");
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setApiError("");

    // Read from form so autofilled values are captured even when onChange
    // hasn't fired yet (common with browser/password-manager autofill).
    const form = e.currentTarget;
    let { email: submittedEmail, password: submittedPassword } =
      readSubmittedCredentials(form);

    // Some autofill providers commit values right after the first user
    // gesture. Re-check once on the next frame before validating.
    if (!submittedEmail && !submittedPassword) {
      await new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => resolve());
      });

      ({ email: submittedEmail, password: submittedPassword } =
        readSubmittedCredentials(form));
    }

    // Keep controlled state in sync with submitted values.
    if (submittedEmail !== email) setEmail(submittedEmail);
    if (submittedPassword !== password) setPassword(submittedPassword);

    // Client-side validation
    const errors = validate(submittedEmail, submittedPassword);
    if (errors.email || errors.password) {
      setFieldErrors(errors);
      return;
    }

    try {
      const response = await login({
        email: submittedEmail,
        password: submittedPassword,
      }).unwrap();
      const { data } = response;

      // Decode id_token to extract user info (name, email, sub)
      const idPayload = JSON.parse(atob(data.id_token.split(".")[1]));

      dispatch(
        setAuthenticated({
          id: idPayload.sub,
          // The local user record is authoritative because Cognito may retain
          // stale attributes when an email is deleted and reinvited.
          displayName: data.name || submittedEmail,
          email: data.email || submittedEmail,
          roles: data.roles,
          permissions: data.permissions,
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
          cognitoUsername: data.cognito_username,
        }),
      );
      toast.success("Login successful! Welcome back.");
      navigate("/", { replace: true });
    } catch (err: unknown) {
      // Map API error status codes to user-friendly messages
      const status = (err as { status?: number })?.status;
      if (status === 401 || status === 403) {
        setApiError("Invalid email or password. Please try again.");
      } else if (status === 429) {
        setApiError(
          "Too many login attempts. Please wait a moment and try again.",
        );
      } else {
        setApiError("An unexpected error occurred. Please try again later.");
      }
    }
  }

  const emailInputId = "login-email";
  const passwordInputId = "login-password";

  return (
    <PublicPageLayout>
      <>
        <div className="w-[420px] max-w-[90%] rounded-lg border border-border bg-surface p-11 shadow-e1">
          <form onSubmit={handleSubmit}>
            <h2 className="text-[22px] font-semibold">Sign in</h2>
            <p className="mb-6 text-[13px] text-sec">
              Use your organization credentials.
            </p>
            {apiError && (
              <div className="min-h-[42px] mb-4 p-3 bg-[var(--error)]/10 border border-[var(--error)]/20 rounded-lg text-[var(--error)] text-sm">
                {apiError}
              </div>
            )}
            <div className="mb-4">
              <Input
                id={emailInputId}
                type="email"
                name="email"
                label="Email"
                placeholder="name@company.com"
                value={email}
                onChange={handleEmailChange}
                error={!!fieldErrors.email}
                errorMessage={fieldErrors.email}
                className="bg-white"
                size="md"
                required
              />
            </div>

            <div className="mb-2">
              <Input
                id={passwordInputId}
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
            <div className="mb-6 text-right">
              <button
                type="button"
                className="text-[12.5px] font-semibold text-accent hover:underline"
                onClick={() => {
                  setResetEmail("");
                  setForgotOpen(true);
                }}
              >
                Forgot password?
              </button>
            </div>
            <Button
              type="submit"
              className="w-full"
              size="md"
              loading={isLoading}
              loadingText="Signing in..."
            >
              Sign in
            </Button>
          </form>
        </div>

        {/* Reset Code Sent Modal */}
        <Modal
          isOpen={forgotOpen}
          onClose={() => setForgotOpen(false)}
          title="Reset your password"
          footer={
            <>
              <Button variant="ghost" onClick={() => setForgotOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={sendResetCode}
                loading={isSendingReset}
                disabled={!canSendReset}
              >
                Send reset code
              </Button>
            </>
          }
        >
          <p className="mb-3 text-[13px] text-sec">
            Enter your account email and we'll send a password reset code to
            reset your password.
          </p>
          <div>
            <Input
              label="Email"
              value={resetEmail}
              required
              onChange={(e) => {
                setResetEmail(e.target.value);
                if (resetEmailError) setResetEmailError("");
              }}
              placeholder="you@company.com"
              error={!!resetEmailError}
              errorMessage={resetEmailError}
              className="bg-white"
            />
          </div>
        </Modal>
      </>
    </PublicPageLayout>
  );
}
