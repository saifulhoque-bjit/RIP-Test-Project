/**
 * Pick the name shown for the signed-in user.
 *
 * The RIP user record (`name` on the login response) wins; the Cognito
 * id_token's `name` claim is only a fallback, since a reused Cognito identity
 * can still carry an earlier invitee's name. Falls back to the email last.
 *
 * SPEC: spec-6f88a1
 */
export function resolveDisplayName(
  dbName: string | null | undefined,
  tokenName: string | null | undefined,
  email: string,
): string {
  return dbName?.trim() || tokenName?.trim() || email;
}
