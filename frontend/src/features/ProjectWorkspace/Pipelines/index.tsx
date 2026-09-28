import { useState } from "react";
import { Table } from "@/components/common/Table";
import { createPipelineRunColumns } from "./pipelineRunsTableColumns";
import { RunJourney } from "./components/RunJourney";
import { useGetIngestionListQuery } from "@/services/api/modules/sources";
import { useGetProjectQuery } from "@/services/api/modules/projects";
import { useNavigate, useParams } from "react-router-dom";
import type { IngestionItem } from "@/types";

export default function Pipelines() {
  const navigate = useNavigate();
  const { id: projectId } = useParams<{ id: string }>();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const {
    data: ingestionResponse,
    isLoading,
    error,
  } = useGetIngestionListQuery(
    { projectId: projectId!, skip: 0, limit: 100 },
    { skip: !projectId },
  );

  // Needed to know whether every user story is already approved — in which
  // case no row's Review button has anything left to send the user to.
  // No refetchOnMountOrArgChange here: the approve mutation (bulkStatusRequirements)
  // already invalidates this exact { type: "Project", id: projectId } cache
  // entry, so RTK Query refetches it on its own the next time anything
  // subscribes — forcing it again on every mount would just be redundant traffic.
  const { data: projectResponse } = useGetProjectQuery(projectId!, {
    skip: !projectId,
  });
  const project = projectResponse?.data;

  const runs = ingestionResponse?.data?.items ?? [];
  const hasError = !!error;

  // Defaults to the top row whenever nothing (or a now-gone run) is selected.
  const selectedRun =
    runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null;

  const handleReviewClick = (run: IngestionItem) => {
    const params = new URLSearchParams({
      source_type: run.source_type,
      ingestion_id: run.id,
    });
    navigate(`/projects/${run.project_id}/review?${params.toString()}`);
  };

  const columns = createPipelineRunColumns({
    onReviewClick: handleReviewClick,
    project,
  });

  return (
    <div className="h-full w-full flex flex-col gap-4.5">
      <Table
        title="Pipeline Runs"
        actionHelperText="Background jobs · statuses update automatically"
        columns={columns}
        data={isLoading || hasError ? [] : runs}
        onRowClick={(run) => setSelectedRunId(run.id)}
        getRowClassName={(run) =>
          run.id === selectedRun?.id ? "bg-accent-50" : undefined
        }
        emptyMessage={
          isLoading
            ? "Loading…"
            : hasError
              ? "Failed to load pipeline runs."
              : "No pipeline runs available"
        }
        striped
      />

      {selectedRun && (
        <RunJourney run={selectedRun} onReviewClick={handleReviewClick} />
      )}
    </div>
  );
}
