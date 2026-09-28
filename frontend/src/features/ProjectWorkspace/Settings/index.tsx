import { useEffect } from "react";
import { useLocation, useParams } from "react-router-dom";

import Loader from "@/components/common/Loader";
import { useGetProjectQuery } from "@/services/api/modules/projects";
import { IdentitySection } from "@/features/ProjectWorkspace/Settings/components/IdentitySection";
import { JiraSection } from "@/features/ProjectWorkspace/Settings/components/JiraSection";
import { TapSection } from "@/features/ProjectWorkspace/Settings/components/TapSection";

export default function Settings() {
  const { id: projectId } = useParams<{ id: string }>();
  const location = useLocation();

  const { data: projectData, isLoading: isProjectLoading } = useGetProjectQuery(
    projectId ?? "",
    { skip: !projectId },
  );
  const project = projectData?.data;

  useEffect(() => {
    if (isProjectLoading || !project || !["#tap-sync", "#jira-sync"].includes(location.hash)) {
      return;
    }

    const settingsCard = document.getElementById(location.hash.slice(1));
    const scrollContainer = settingsCard?.closest("main");
    if (!settingsCard || !(scrollContainer instanceof HTMLElement)) {
      return;
    }

    let settleFrameId: number | undefined;
    const frameId = window.requestAnimationFrame(() => {
      settleFrameId = window.requestAnimationFrame(() => {
        const containerRect = scrollContainer.getBoundingClientRect();
        const cardRect = settingsCard.getBoundingClientRect();
        const targetTop =
          scrollContainer.scrollTop + cardRect.top - containerRect.top;

        scrollContainer.scrollTo({ top: targetTop, behavior: "smooth" });
      });
    });

    return () => {
      window.cancelAnimationFrame(frameId);
      if (settleFrameId !== undefined) {
        window.cancelAnimationFrame(settleFrameId);
      }
    };
  }, [
    isProjectLoading,
    location.hash,
    project,
  ]);

  if (isProjectLoading || !project || !projectId) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader ariaLabel="Loading project settings" subtitle="Loading settings..." />
      </div>
    );
  }

  return (
    <div className="w-full">
      <IdentitySection project={project} />
      <JiraSection projectId={projectId} />
      <TapSection projectId={projectId} />
    </div>
  );
}
