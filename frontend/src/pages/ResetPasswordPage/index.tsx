// Reset Password Page — public route (/reset-password?email=...)
// Reached automatically after a successful forgot-password request. The user
// pastes in the 6-digit code emailed to them and sets a new password via
// POST /auth/reset-password.
import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { toast } from "@/lib/toast";
import { validators } from "@/constants/validators";
import { getErrorMessage } from "@/utils/getErrorMessage";
import { useResetPasswordMutation } from "@/services/api/modules/auth";
import Button from "@/components/common/Button/Button";
import Input from "@/components/common/Input";
import { EyeIcon } from "@/assets/icons/EyeIcon";
import { EyeOffIcon } from "@/assets/icons/EyeOffIcon";
import PublicPageLayout from "@/components/layout/PublicPageLayout";

// ── Inline field validation ───────────────────────────────────────────────────

interface FieldErrors {
  code: string;
  password: string;
  confirmPassword: string;
}

function validate(
  code: string,
  password: string,
  confirmPassword: string,
): FieldErrors {
  const errors: FieldErrors = { code: "", password: "", confirmPassword: "" };

  const codeValidation = validators.otpCode(code);
  if (!codeValidation.isValid) {
    errors.code = codeValidation.error || "";
  }

  const passwordValidation = validators.passwordStrong(password);
  if (!passwordValidation.isValid) {
    errors.password = passwordValidation.error || "";
  }

  const matchValidation = validators.match(
    password,
    confirmPassword,
    "Passwords",
  );
  if (!confirmPassword) {
    errors.confirmPassword = "Please confirm your password.";
  } else if (!matchValidation.isValid) {
    errors.confirmPassword = matchValidation.error || "";
  }

  return errors;
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function ResetPasswordPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const email = searchParams.get("email") ?? "";

  const [resetPassword, { isLoading: isResetting }] =
    useResetPasswordMutation();

  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({
    code: "",
    password: "",
    confirmPassword: "",
  });
  const [apiError, setApiError] = useState("");

  function handleCodeChange(e: React.ChangeEvent<HTMLInputElement>) {
    setCode(e.target.value);
    if (fieldErrors.code) setFieldErrors((prev) => ({ ...prev, code: "" }));
    if (apiError) setApiError("");
  }

  function handlePasswordChange(e: React.ChangeEvent<HTMLInputElement>) {
    setPassword(e.target.value);
    if (fieldErrors.password)
      setFieldErrors((prev) => ({ ...prev, password: "" }));
    if (apiError) setApiError("");
  }

  function handleConfirmPasswordChange(
    e: React.ChangeEvent<HTMLInputElement>,
  ) {
    setConfirmPassword(e.target.value);
    if (fieldErrors.confirmPassword)
      setFieldErrors((prev) => ({ ...prev, confirmPassword: "" }));
    if (apiError) setApiError("");
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setApiError("");

    const errors = validate(code, password, confirmPassword);
    if (errors.code || errors.password || errors.confirmPassword) {
      setFieldErrors(errors);
      return;
    }

    try {
      await resetPassword({
        email,
        code: code.trim(),
        new_password: password,
      }).unwrap();

      toast.success("Your password has been reset. Please sign in.");
      navigate("/login", { replace: true });
    } catch (err: unknown) {
      setApiError(
        getErrorMessage(
          err,
          "Could not reset your password. The code may be wrong or expired.",
        ),
      );
    }
  }

  if (!email) {
    return (
      <PublicPageLayout>
        <div className="w-[420px] max-w-[90%] rounded-lg border border-border bg-surface p-11 shadow-e1">
          <h2 className="text-[22px] font-semibold">Missing email</h2>
          <p className="mt-2 text-[13px] text-sec">
            We couldn't tell which account to reset. Please request a new
            reset code from the sign-in page.
          </p>
          <Button
            className="mt-4 w-full"
            size="md"
            onClick={() => navigate("/login", { replace: true })}
          >
            Back to sign in
          </Button>
        </div>
      </PublicPageLayout>
    );
  }

  return (
    <PublicPageLayout>
      <div className="w-[420px] max-w-[90%] rounded-lg border border-border bg-surface p-11 shadow-e1">
        <form onSubmit={handleSubmit}>
          <h2 className="text-[22px] font-semibold">Reset your password</h2>
          <p className="mb-6 text-[13px] text-sec">
            Enter the 6-digit code we sent to{" "}
            <b className="font-semibold text-primary">{email}</b> and choose a
            new password.
          </p>

          {apiError && (
            <div className="min-h-[42px] mb-4 p-3 bg-[var(--error)]/10 border border-[var(--error)]/20 rounded-lg text-[var(--error)] text-sm">
              {apiError}
            </div>
          )}

          <div className="mb-4">
            <Input
              id="reset-code"
              name="code"
              label="Reset code"
              inputMode="numeric"
              maxLength={6}
              placeholder="123456"
              value={code}
              onChange={handleCodeChange}
              error={!!fieldErrors.code}
              errorMessage={fieldErrors.code}
              className="bg-white"
              size="md"
              required
            />
          </div>

          <div className="mb-4">
            <Input
              id="reset-new-password"
              name="password"
              label="New password"
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
              endIconAriaLabel={showPassword ? "Hide password" : "Show password"}
              onEndIconClick={() => setShowPassword((prev) => !prev)}
              size="md"
              required
            />
          </div>

          <div className="mb-6">
            <Input
              id="reset-confirm-password"
              name="confirm_password"
              label="Confirm new password"
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
              required
            />
          </div>

          <Button
            type="submit"
            className="w-full"
            size="md"
            loading={isResetting}
            loadingText="Resetting password..."
          >
            Reset password
          </Button>

          <p className="mt-4 text-center text-[12.5px] text-sec">
            <Link to="/login" className="font-semibold text-accent hover:underline">
              Back to sign in
            </Link>
          </p>
        </form>
      </div>
    </PublicPageLayout>
  );
}
