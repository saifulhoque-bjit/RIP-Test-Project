import { useParams } from "react-router-dom";
import ModuleFeatureStoryWorkspace from "@/features/ProjectWorkspace/Review/components/middle-panel/ModuleFeatureStoryWorkspace";
import ReviewProvider from "@/features/ProjectWorkspace/Review/components/ReviewProvider";

export default function Requirements() {
  const { id: projectId } = useParams<{ id: string }>();

  return (
    <ReviewProvider projectId={projectId}>
      <div className="relative flex h-full w-full flex-col items-center justify-start gap-4">
        <ModuleFeatureStoryWorkspace
          approvedOnly
          enableFilters={false}
          enableApprove={false}
          enableFeedback={false}
        />
      </div>
    </ReviewProvider>
  );
}
