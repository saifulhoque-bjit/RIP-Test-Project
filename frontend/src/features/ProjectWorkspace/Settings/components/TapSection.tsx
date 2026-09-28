import { useEffect, useState } from "react";
import { toast } from "@/lib/toast";

import Card from "@/components/common/Card";
import { Chip } from "@/components/common/Chip";
import Input from "@/components/common/Input";
import Button from "@/components/common/Button/Button";
import { EyeIcon } from "@/assets/icons/EyeIcon";
import { EyeOffIcon } from "@/assets/icons/EyeOffIcon";
import { useAppDispatch } from "@/store/hooks";
import {
  clearTapIntegration,
  setTapIntegration,
} from "@/store/slices/tapIntegrationSlice";
import {
  useGetProjectTapIntegrationQuery,
  useCreateProjectTapIntegrationMutation,
  useUpdateProjectTapIntegrationMutation,
} from "@/services/api/modules/tapIntegration";
import { getErrorMessage } from "@/utils/getErrorMessage";
import { hasRole } from "@/utils/hasRole";
import { USER_ROLE } from "@/types/auth";

/**
 * RIP always identifies to TAP as this app client — it is a property of the
 * product, not of a project, so it is shown read-only and never submitted.
 * Mirrors `TAP_APP_CLIENT_NAME` in the backend, which is what actually gets
 * sent to TAP; this value is only a fallback for a project that has no
 * integration row yet (the API echoes the real one once connected).
 */
const TAP_APP_CLIENT_NAME = "RIP";

export function TapSection({ projectId }: { projectId: string }) {
  const isAdmin = hasRole(USER_ROLE.CLIENT_ADMIN);

  const dispatch = useAppDispatch();

  // `currentData` (not `data`): this section stays mounted across a project
  // switch, and `data` keeps serving the PREVIOUS project's config until the
  // new one succeeds — permanently so when the new project has no TAP row
  // (404), which left the old Base URL / Client ID / "Connected" on screen.
  const {
    currentData: loadedIntegration,
    error: integrationError,
    isFetching,
    isUninitialized,
  } = useGetProjectTapIntegrationQuery(projectId);

  // Loading = "no config for THIS project yet". `isLoading` alone only covers
  // the hook's very first fetch, so a project switch has to read as loading
  // too; keeping the data in the condition means the refetch after a save
  // doesn't blank the form the user is looking at.
  const isLoadingIntegration =
    isUninitialized || (isFetching && !loadedIntegration);

  // A 404 ("No active TAP integration found for project …") is the "never
  // connected yet" state, not a failure — same shape JiraSection uses.
  // Anything else that isn't a 404 is a real load error, which is what the
  // two flags below distinguish.
  const isNotConfigured =
    !!integrationError &&
    "status" in integrationError &&
    integrationError.status === 404;

  // That 404 is authoritative for the current project: RTK Query keeps the
  // last good `data` on a *rejected refetch*, so a row removed on the server
  // would otherwise keep this card on "Connected" against a config the API
  // says no longer exists.
  const integration = isNotConfigured ? undefined : loadedIntegration;

  const isConfigured = !!integration;

  const [createIntegration, { isLoading: isCreating }] =
    useCreateProjectTapIntegrationMutation();
  const [updateIntegration, { isLoading: isUpdating }] =
    useUpdateProjectTapIntegrationMutation();
  const isSaving = isCreating || isUpdating;

  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clientId, setClientId] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);

  // Mirror this project's config into the shared slice. The 404 branch
  // matters: it is the API's "no active TAP integration for this project"
  // answer, so the slice entry has to be dropped as well — the slice only
  // ever grew before, leaving a phantom config behind for a project whose
  // integration was removed (or was never this project's to begin with).
  useEffect(() => {
    if (integration) {
      dispatch(setTapIntegration({ projectId, integration }));
    } else if (isNotConfigured) {
      dispatch(clearTapIntegration(projectId));
    }
  }, [integration, isNotConfigured, projectId, dispatch]);

  // Populate the form once per project from the loaded config; never
  // repopulate the api_key. Keyed on `projectId`, not `integration.id`: a
  // project with no TAP config has no id to key on, so that key never fired
  // on a switch and the previous project's values stayed in the inputs.
  if (!isLoadingIntegration && hydratedFor !== projectId) {
    setBaseUrl(integration?.base_url ?? "");
    setClientId(integration?.client_id ?? "");
    setApiKey("");
    setHydratedFor(projectId);
  }

  const canSave =
    !!baseUrl.trim() &&
    !!clientId.trim() &&
    (isConfigured || !!apiKey.trim());

  const isDirty = integration
    ? baseUrl !== integration.base_url ||
      clientId !== integration.client_id ||
      !!apiKey.trim()
    : !!baseUrl.trim() ||
      !!clientId.trim() ||
      !!apiKey.trim();

  const handleCancel = () => {
    if (integration) {
      setBaseUrl(integration.base_url);
      setClientId(integration.client_id);
    } else {
      setBaseUrl("");
      setClientId("");
    }
    setApiKey("");
  };

  /**
   * Save = verify + persist, in one request.
   *
   * The server calls TAP with these credentials and only writes the row (and
   * flips the project to connected) once TAP accepts them, so there is no
   * separate verify round-trip to make here — and no way for the UI to show
   * "Connected" against credentials TAP never approved. On update the server
   * re-verifies with the stored key when the user did not retype it.
   */
  const handleVerifyAndSave = async () => {
    if (!canSave) return;

    const apiKeyToUse = apiKey.trim();

    try {
      const body = {
        base_url: baseUrl.trim(),
        client_id: clientId.trim(),
        ...(apiKeyToUse ? { api_key: apiKeyToUse } : {}),
      };

      const result = isConfigured
        ? await updateIntegration({ projectId, body }).unwrap()
        : await createIntegration({
            projectId,
            body: { ...body, api_key: apiKeyToUse },
          }).unwrap();

      dispatch(setTapIntegration({ projectId, integration: result }));
      // Re-sync from the saved row: the form hydrates only once per project,
      // so without this the inputs keep the untrimmed text the user typed and
      // the section still reads as dirty right after a successful save.
      setBaseUrl(result.base_url);
      setClientId(result.client_id);
      setApiKey("");
      toast.success("TAP settings saved.");
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          "Could not verify TAP credentials. Please check the Base URL, App Client Name, API Key and Client ID.",
        ),
      );
    }
  };

  return (
    <>
      <Card id="tap-sync" className="mb-[18px]">
        <header className="flex items-center justify-between border-b border-[var(--border-primary)] px-[18px] py-3.5">
          <div>
            <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">
              TAP Sync
            </h3>
            <p className="mt-0.5 text-[12.5px] text-[var(--text-tertiary)]">
              Sync approved requirements to TAP via app client credentials.
            </p>
          </div>
          {isConfigured ? (
            <Chip tone="ok" dot>
              Connected
            </Chip>
          ) : (
            <Chip tone="off" dot>
              Not configured
            </Chip>
          )}
        </header>

        <div className="p-[18px]">
          {isLoadingIntegration ? (
            <div className="py-4 text-center text-[12.5px] text-mut">
              Loading TAP settings…
            </div>
          ) : (
            <>
              {!isConfigured && !isNotConfigured && (
                <div className="mb-3.5 text-[12.5px] text-[var(--error)]">
                  Could not load this project&apos;s TAP settings. Try
                  refreshing the page.
                </div>
              )}

              <div className="grid grid-cols-1 gap-[18px] md:grid-cols-2">
                <Input
                  label="Base URL"
                  value={baseUrl}
                  required
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://tap.yourcompany.com"
                  disabled={!isAdmin}
                />
                <Input
                  label="App Client Name"
                  value={integration?.app_client_name ?? TAP_APP_CLIENT_NAME}
                  readOnly
                  disabled
                  hint="Fixed — RIP always connects to TAP under this app client."
                />
                <Input
                  label="Client ID"
                  autoComplete="off"
                  required
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  disabled={!isAdmin}
                />
                <Input
                  label="API Key"
                  type={showApiKey ? "text" : "password"}
                  required
                  autoComplete="new-password"
                  hint={
                    isConfigured
                      ? `Current: ${integration?.api_key_hint}. Leave blank to keep it unchanged.`
                      : "Provided by the TAP service administrator."
                  }
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={
                    isConfigured ? "Leave blank to keep current key" : "sk-…"
                  }
                  endIcon={
                    showApiKey ? (
                      <EyeOffIcon className="h-4 w-4" />
                    ) : (
                      <EyeIcon className="h-4 w-4" />
                    )
                  }
                  endIconAriaLabel={showApiKey ? "Hide key" : "Show key"}
                  onEndIconClick={() => setShowApiKey((v) => !v)}
                  disabled={!isAdmin}
                />
              </div>
            </>
          )}
        </div>
      </Card>

      {!isLoadingIntegration && isAdmin && (
        <div className="flex justify-end gap-2.5 pb-[18px]">
          <Button
            size="sm"
            variant="ghost"
            disabled={!isDirty || isSaving}
            onClick={handleCancel}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!canSave || !isDirty}
            loading={isSaving}
            onClick={() => void handleVerifyAndSave()}
          >
            {isConfigured ? "Save changes" : "Verify & Connect"}
          </Button>
        </div>
      )}
    </>
  );
}
