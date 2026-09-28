import type { TenantLlmProvider } from "@/types";

export type ProviderIssue = "missing" | "not_verified" | "not_active";
export type ProviderSetupIssue =
  | "all_missing"
  | "none_verified"
  | "none_active";

export const providerLabel = (id: string) =>
  id.charAt(0).toUpperCase() + id.slice(1);

// A project's LLM provider is only usable once the tenant has saved a key for
// it, that key has been verified, and the provider has been switched on —
// any earlier stage should block file uploads before they fail server-side.
export function getProviderIssue(
  providerId: string | undefined,
  items: TenantLlmProvider[] | undefined,
): ProviderIssue | null {
  if (!providerId) return null;
  const item = items?.find((p) => p.provider === providerId);
  if (!item || !item.has_api_key) return "missing";
  if (!item.is_verified) return "not_verified";
  if (!item.is_active) return "not_active";
  return null;
}

export function getProviderSetupIssue(
  providerIds: string[],
  items: TenantLlmProvider[] | undefined,
): ProviderSetupIssue | null {
  const configuredProviders = providerIds
    .map((providerId) => items?.find((item) => item.provider === providerId))
    .filter(
      (provider): provider is TenantLlmProvider =>
        !!provider?.has_api_key,
    );

  if (configuredProviders.length === 0) return "all_missing";

  const verifiedProviders = configuredProviders.filter(
    (provider) => provider.is_verified,
  );
  if (verifiedProviders.length === 0) return "none_verified";

  const activeVerifiedProvider = verifiedProviders.some(
    (provider) => provider.is_active,
  );
  if (!activeVerifiedProvider) return "none_active";

  return null;
}

export function getAdminProviderSetupMessage(issue: ProviderSetupIssue): string {
  switch (issue) {
    case "all_missing":
      return "No LLM provider has an API key configured. Please set one up from Clients & Providers settings.";
    case "none_verified":
      return "No LLM provider has a verified API key. Please verify one from Clients & Providers settings.";
    case "none_active":
      return "No verified LLM provider is active. Please activate one from Clients & Providers settings.";
  }
}

export function getMemberProviderMessage(
  issue: ProviderIssue,
  label: string,
): string {
  switch (issue) {
    case "missing":
      return `The API key for the ${label} LLM provider has not been set yet. Please contact your admin to set it up.`;
    case "not_verified":
      return `The API key for the ${label} LLM provider could not be verified. Please contact your admin to fix it.`;
    case "not_active":
      return `The ${label} LLM provider is not active yet. Please contact your admin to activate it.`;
  }
}

export function getAdminProviderMessage(
  issue: ProviderIssue,
  label: string,
): string {
  switch (issue) {
    case "missing":
      return `The API key for the ${label} LLM provider has not been set yet. Please set it up from Clients & Providers settings.`;
    case "not_verified":
      return `The API key for the ${label} LLM provider could not be verified. Please verify it from Clients & Providers settings.`;
    case "not_active":
      return `The ${label} LLM provider is set up but not active yet. Please activate it from Clients & Providers settings.`;
  }
}
