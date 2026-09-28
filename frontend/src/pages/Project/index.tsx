import { lazy, Suspense, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import Loader from "@/components/common/Loader";
import { useGetProjectQuery } from "@/services/api/modules/projects";
import { useGetProjectJiraIntegrationQuery } from "@/services/api/modules/jira";
import { useGetProjectTapIntegrationQuery } from "@/services/api/modules/tapIntegration";
import { PageHeaderPortal } from "@/components/common/PageHeaderPortal";
import SubHeader from "@/features/ProjectWorkspace/SubHeader";
import { PROJECT_TABS, type ProjectTabKey } from "@/constants/projectTabs";
import Button from "@/components/common/Button/Button";
import Modal from "@/components/common/Modal";
import { useProjectTasksSocket } from "@/hooks/useProjectTasksSocket";
import { useStoryFeedbackRegenerationResolver } from "@/features/ProjectWorkspace/Review/hooks/useStoryFeedbackRegenerationStatus";
import { useFeatureFeedbackRegenerationResolver } from "@/features/ProjectWorkspace/Review/hooks/useFeatureFeedbackRegenerationStatus";
import {
  SyncTray,
  type SyncTarget,
} from "@/features/ProjectWorkspace/Sync/SyncTray";
import { ExportModal } from "@/features/ProjectWorkspace/Export/ExportModal";
import { ArrowNarrowRight } from "@/assets/icons/arrow/ArrowNarrowRight";
import { ArrowSwitch } from "@/assets/icons/arrow/ArrowSwitch";
import {
  calculatePendingJiraSync,
  calculatePendingTapSync,
} from "@/utils/projectMetrics";

const Overview = lazy(() => import("@/features/ProjectWorkspace/Overview"));
const Sources = lazy(() => import("@/features/ProjectWorkspace/Sources"));
const Pipelines = lazy(() => import("@/features/ProjectWorkspace/Pipelines"));
const Review = lazy(() => import("@/features/ProjectWorkspace/Review"));
const Requirements = lazy(
  () => import("@/features/ProjectWorkspace/Requirements"),
);
const Settings = lazy(() => import("@/features/ProjectWorkspace/Settings"));
const Activity = lazy(() => import("@/features/ProjectWorkspace/Activity"));

export default function ProjectPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const [syncTarget, setSyncTarget] = useState<SyncTarget | null>(null);
  const [isExportOpen, setIsExportOpen] = useState(false);
  const [isTapSettingsWarningOpen, setIsTapSettingsWarningOpen] =
    useState(false);
  const [isJiraSettingsWarningOpen, setIsJiraSettingsWarningOpen] =
    useState(false);

  // One connection for the whole project workspace — every tab below reads
  // live task status from Redux instead of opening its own socket.
  useProjectTasksSocket(projectId);

  // Must run regardless of which tab is active — Review unmounts on tab
  // switch, so resolving a pending feedback-regeneration task can't live
  // there or it'd get stuck until the user happens to come back.
  useStoryFeedbackRegenerationResolver(projectId);
  useFeatureFeedbackRegenerationResolver(projectId);

  // `currentData` (not `data`) so a project switch doesn't briefly render
  // the PREVIOUS project's header/name while the new one is still loading.
  const { currentData: projectData, isLoading: isProjectLoading } =
    useGetProjectQuery(projectId ?? "", { skip: !projectId });
  const { data: tapIntegration, isLoading: isTapIntegrationLoading } =
    useGetProjectTapIntegrationQuery(projectId ?? "", { skip: !projectId });
  const { data: jiraIntegration, isLoading: isJiraIntegrationLoading } =
    useGetProjectJiraIntegrationQuery(projectId ?? "", { skip: !projectId });
  const project = projectData?.data;

  const pendingJiraSync = calculatePendingJiraSync(project);
  const pendingTapSync = calculatePendingTapSync(project);

  const handleTapSyncClick = () => {
    if (isTapIntegrationLoading) return;

    if (!tapIntegration) {
      setIsTapSettingsWarningOpen(true);
      return;
    }

    setSyncTarget("tap");
  };

  const handleJiraSyncClick = () => {
    if (isJiraIntegrationLoading) return;

    if (!jiraIntegration) {
      setIsJiraSettingsWarningOpen(true);
      return;
    }

    setSyncTarget("jira");
  };

  const hasUserStories = (project?.user_stories ?? 0) > 0;
  const hasApprovedStories = (project?.approved_user_stories ?? 0) > 0;

  // Derive active tab from current URL
  const activeTab = useMemo<ProjectTabKey>(() => {
    const path = location.pathname;
    const matched = PROJECT_TABS.find(
      (t) => projectId && path === t.buildPath(projectId),
    );
    return matched?.key ?? "sources";
  }, [location.pathname, projectId]);

  if (isProjectLoading || !project) {
    return (
      <div className="flex items-center justify-center min-h-[300px]">
        <Loader ariaLabel="Loading project" subtitle="Loading project..." />
      </div>
    );
  }

  return (
    <>
      <PageHeaderPortal>
        <SubHeader
          project={project}
          actions={
            <div className="flex gap-2.5">
              <Button
                variant="ghost"
                size="xs"
                iconLeading={<ArrowNarrowRight className="w-3 h-3 rotate-90" />}
                disabled={!hasUserStories}
                onClick={() => setIsExportOpen(true)}
              >
                Export
              </Button>
              <Button
                size="xs"
                iconLeading={<ArrowSwitch className="w-3 h-3" />}
                disabled={!hasApprovedStories}
                onClick={handleTapSyncClick}
              >
                TAP Sync
                <span className="ml-1 rounded bg-white/25 px-1.5 py-[2px] text-white inline-flex justify-center items-center">
                  {pendingTapSync}
                </span>
              </Button>
              <Button
                size="xs"
                iconLeading={<ArrowSwitch className="w-3 h-3" />}
                disabled={!hasApprovedStories}
                onClick={handleJiraSyncClick}
              >
                Jira Sync
                <span className="ml-1 rounded bg-white/25 px-1.5 py-[2px] text-white inline-flex justify-center items-center">
                  {pendingJiraSync}
                </span>
              </Button>
            </div>
          }
        />
      </PageHeaderPortal>

      <div className="flex-1 min-h-0 px-6 py-5">
        <Suspense fallback={<LoadingFallback />}>
          {activeTab === "overview" && <Overview />}
          {activeTab === "sources" && <Sources />}
          {activeTab === "pipelines" && <Pipelines />}
          {activeTab === "requirements" && <Requirements />}
          {activeTab === "review" && <Review />}
          {activeTab === "settings" && <Settings />}
          {activeTab === "activity" && <Activity />}
        </Suspense>
      </div>

      {syncTarget && (
        <SyncTray
          projectId={projectId ?? ""}
          projectName={project.name ?? ""}
          target={syncTarget ?? "jira"}
          isOpen={syncTarget !== null}
          onClose={() => setSyncTarget(null)}
        />
      )}

      <Modal
        isOpen={isTapSettingsWarningOpen}
        onClose={() => setIsTapSettingsWarningOpen(false)}
        title="TAP sync is not configured"
        width={520}
        footer={
          <div className="flex gap-2.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setIsTapSettingsWarningOpen(false)}
              className="mr-auto"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setIsTapSettingsWarningOpen(false);
                navigate(
                  `${PROJECT_TABS.find((tab) => tab.key === "settings")!.buildPath(
                    project.id,
                  )}#tap-sync`,
                );
              }}
            >
              Go to Project Settings
            </Button>
          </div>
        }
      >
        <p className="text-[13px] text-[var(--text-secondary)]">
          Configure and verify the TAP connection for this project before
          starting a TAP sync.
        </p>
      </Modal>

      <Modal
        isOpen={isJiraSettingsWarningOpen}
        onClose={() => setIsJiraSettingsWarningOpen(false)}
        title="Jira sync is not configured"
        width={520}
        footer={
          <div className="flex gap-2.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setIsJiraSettingsWarningOpen(false)}
              className="mr-auto"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setIsJiraSettingsWarningOpen(false);
                navigate(
                  `${PROJECT_TABS.find((tab) => tab.key === "settings")!.buildPath(
                    project.id,
                  )}#jira-sync`,
                );
              }}
            >
              Go to Project Settings
            </Button>
          </div>
        }
      >
        <p className="text-[13px] text-[var(--text-secondary)]">
          Configure and verify the Jira connection for this project before
          starting a Jira sync.
        </p>
      </Modal>

      {isExportOpen && (
        <ExportModal
          isOpen={isExportOpen}
          onClose={() => setIsExportOpen(false)}
          project={project}
        />
      )}
    </>
  );
}

function LoadingFallback() {
  return (
    <div className="flex items-center justify-center min-h-[200px] w-full">
      <Loader />
    </div>
  );
}
