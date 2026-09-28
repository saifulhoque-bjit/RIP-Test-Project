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
  clearJiraIntegration,
  setJiraIntegration,
} from "@/store/slices/jiraIntegrationSlice";
import {
  useGetProjectJiraIntegrationQuery,
  useCreateProjectJiraIntegrationMutation,
  useUpdateProjectJiraIntegrationMutation,
} from "@/services/api/modules/jira";
import { getErrorMessage } from "@/utils/getErrorMessage";
import { hasRole } from "@/utils/hasRole";
import { USER_ROLE } from "@/types/auth";

export function JiraSection({ projectId }: { projectId: string }) {
  // Only the client admin role can edit project settings — super_admin and
  // member both get a read-only view here.
  const isAdmin = hasRole(USER_ROLE.CLIENT_ADMIN);

  const dispatch = useAppDispatch();

  // `currentData` (not `data`): this section stays mounted across a project
  // switch, and `data` keeps serving the PREVIOUS project's config until the
  // new one succeeds — permanently so when the new project has no Jira row
  // (404), which left the old Base URL / project key / "Connected" on screen.
  const {
    currentData: loadedIntegration,
    error: integrationError,
    isFetching,
    isUninitialized,
  } = useGetProjectJiraIntegrationQuery(projectId);

  // Loading = "no config for THIS project yet". `isLoading` alone only covers
  // the hook's very first fetch, so a project switch has to read as loading
  // too; keeping the data in the condition means the refetch after a save
  // doesn't blank the form the user is looking at.
  const isLoadingIntegration =
    isUninitialized || (isFetching && !loadedIntegration);

  // A 404 just means "no Jira config for this project yet" — not an error.
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
    useCreateProjectJiraIntegrationMutation();
  const [updateIntegration, { isLoading: isUpdating }] =
    useUpdateProjectJiraIntegrationMutation();
  const isSaving = isCreating || isUpdating;

  const [baseUrl, setBaseUrl] = useState("");
  const [projectKey, setProjectKey] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);

  // Keep the global Jira-integration slice in sync with the server cache
  // (initial load and whenever the query refetches after a mutation). The 404
  // branch matters: it is the API's "no active Jira integration for this
  // project" answer, so the slice entry has to be dropped as well — the slice
  // only ever grew before, leaving a phantom config behind for a project whose
  // integration was removed (or was never this project's to begin with).
  useEffect(() => {
    if (integration) {
      dispatch(setJiraIntegration({ projectId, integration }));
    } else if (isNotConfigured) {
      dispatch(clearJiraIntegration(projectId));
    }
  }, [integration, isNotConfigured, projectId, dispatch]);

  // Populate the form once per project from the loaded integration — never
  // re-populate the token field with a real secret; the API only ever returns
  // a masked hint. Keyed on `projectId`, not `integration.id`: a project with
  // no Jira config has no id to key on, so that key never fired on a switch
  // and the previous project's values stayed in the inputs.
  if (!isLoadingIntegration && hydratedFor !== projectId) {
    setBaseUrl(integration?.jira_base_url ?? "");
    setProjectKey(integration?.jira_project_key ?? "");
    setUserEmail(integration?.jira_user_email ?? "");
    setApiToken("");
    setHydratedFor(projectId);
  }

  const canSave =
    !!baseUrl.trim() &&
    !!projectKey.trim() &&
    !!userEmail.trim() &&
    (isConfigured || !!apiToken.trim());

  const isDirty = integration
    ? baseUrl !== integration.jira_base_url ||
      projectKey !== integration.jira_project_key ||
      userEmail !== integration.jira_user_email ||
      !!apiToken.trim()
    : !!baseUrl.trim() ||
      !!projectKey.trim() ||
      !!userEmail.trim() ||
      !!apiToken.trim();

  const handleCancel = () => {
    if (integration) {
      setBaseUrl(integration.jira_base_url);
      setProjectKey(integration.jira_project_key);
      setUserEmail(integration.jira_user_email);
    } else {
      setBaseUrl("");
      setProjectKey("");
      setUserEmail("");
    }
    setApiToken("");
  };

  const handleSave = async () => {
    if (!canSave) return;

    try {
      const result = isConfigured
        ? await updateIntegration({
            projectId,
            body: {
              jira_base_url: baseUrl.trim(),
              jira_project_key: projectKey.trim(),
              jira_user_email: userEmail.trim(),
              ...(apiToken.trim() ? { api_token: apiToken.trim() } : {}),
            },
          }).unwrap()
        : await createIntegration({
            projectId,
            body: {
              jira_base_url: baseUrl.trim(),
              jira_project_key: projectKey.trim(),
              jira_user_email: userEmail.trim(),
              api_token: apiToken.trim(),
            },
          }).unwrap();

      dispatch(setJiraIntegration({ projectId, integration: result }));
      // Re-sync from the saved row: the form hydrates only once per project,
      // so without this the inputs keep the untrimmed text the user typed and
      // the section still reads as dirty right after a successful save.
      setBaseUrl(result.jira_base_url);
      setProjectKey(result.jira_project_key);
      setUserEmail(result.jira_user_email);
      setApiToken("");
      toast.success("Jira settings saved.");
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          "Failed to save Jira settings. Please try again.",
        ),
      );
    }
  };

  return (
    <>
      <Card id="jira-sync" className="mb-[18px]">
        <header className="flex items-center justify-between border-b border-[var(--border-primary)] px-[18px] py-3.5">
          <div>
            <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">
              Jira
            </h3>
            <p className="mt-0.5 text-[12.5px] text-[var(--text-tertiary)]">
              Sync approved requirements to this Jira project.
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
              Loading Jira settings…
            </div>
          ) : (
            <>
              {!isConfigured && !isNotConfigured && (
                <div className="mb-3.5 text-[12.5px] text-[var(--error)]">
                  Could not load this project&apos;s Jira settings. Try
                  refreshing the page.
                </div>
              )}

              <div className="grid grid-cols-1 gap-[18px] md:grid-cols-2">
                <Input
                  label="Base URL"
                  value={baseUrl}
                  required
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://yourcompany.atlassian.net"
                  disabled={!isAdmin}
                />
                <Input
                  label="Project key"
                  value={projectKey}
                  required
                  onChange={(e) => setProjectKey(e.target.value)}
                  placeholder="e.g. MER"
                  disabled={!isAdmin}
                />
                <Input
                  label="User email"
                  type="email"
                  required
                  autoComplete="off"
                  value={userEmail}
                  onChange={(e) => setUserEmail(e.target.value)}
                  placeholder="bot@company.com"
                  disabled={!isAdmin}
                />
                <Input
                  label="API token"
                  type={showToken ? "text" : "password"}
                  required
                  autoComplete="new-password"
                  hint={
                    isConfigured
                      ? `Current: ${integration?.api_token_hint}. Leave blank to keep it unchanged.`
                      : "Generated from your Atlassian account's API token settings."
                  }
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  placeholder={
                    isConfigured
                      ? "Leave blank to keep current token"
                      : "ATATT3xFfGF0…"
                  }
                  endIcon={
                    showToken ? (
                      <EyeOffIcon className="h-4 w-4" />
                    ) : (
                      <EyeIcon className="h-4 w-4" />
                    )
                  }
                  endIconAriaLabel={showToken ? "Hide token" : "Show token"}
                  onEndIconClick={() => setShowToken((v) => !v)}
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
            onClick={() => void handleSave()}
          >
            {isConfigured ? "Save changes" : "Connect Jira"}
          </Button>
        </div>
      )}
    </>
  );
}
