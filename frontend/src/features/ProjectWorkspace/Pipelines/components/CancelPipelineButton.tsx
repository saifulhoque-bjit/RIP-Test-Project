import { useEffect, useState } from "react";
import { toast } from "@/lib/toast";
import { store } from "@/store";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";
import { normalizeIngestionStatus } from "@/features/ProjectWorkspace/Sources/sourceColumns";
import { useLatestProjectTask } from "@/hooks/useProjectTaskStatus";
import { TASK_TYPE, isTerminalTaskStatus, type TaskType } from "@/types/projectTask";
import { useCancelTaskMutation } from "@/services/api/modules/tasks";
import {
  clearPipelineCancellation,
  selectPendingPipelineCancellation,
  trackPipelineCancellation,
} from "@/store/slices/pipelineCancellationSlice";
import type { IngestionItem } from "@/types";

/**
 * RFP runs progress through two ingestion stages ("generating_module_feature"
 * then "generating_user_story"), the current one being the last entry in
 * `stages`. Once an RFP run has moved into "generating_user_story" it's no
 * longer tied to the source file upload itself, so it can't be cancelled
 * from here.
 *
 * source_code runs are NOT staged this way — a single pass produces the
 * UI/API spec, features and user stories together, and the row carries both
 * stage entries for its whole lifetime, so this check must not be applied to
 * them or their Cancel button would never render.
 */
function isRfpPastUploadStage(run: IngestionItem): boolean {
  return (
    run.source_type === "rfp" &&
    run.stages[run.stages.length - 1] === "generating_user_story"
  );
}

/** Only the runs backed by a source_process/incremental_update WS task are cancellable. */
function getCancellableTaskType(run: IngestionItem): TaskType | null {
  if (run.source_type === "rfp" || run.source_type === "source_code") {
    if (isRfpPastUploadStage(run)) {
      return null;
    }
    return TASK_TYPE.SOURCE_PROCESS;
  }
  if (run.source_type === "meeting_notes") {
    return TASK_TYPE.INCREMENTAL_UPDATE;
  }
  return null;
}

export function CancelPipelineButton({ run }: { run: IngestionItem }) {
  const [isConfirming, setIsConfirming] = useState(false);
  const [cancelTask, { isLoading }] = useCancelTaskMutation();
  const dispatch = useAppDispatch();
  /**
   * True from the moment the user confirms cancellation until the run's own
   * row reports stopped. Needed in addition to `isLoading`: the backend
   * flips the live ProjectTask (`liveTask` below) to "cancelled" well before
   * the ingestion row catches up — often before this mutation's HTTP
   * response even comes back — which makes `isCancellable` below go false
   * and would otherwise unmount this button/Modal (replacing it with the
   * plain "-" span) before the "Cancelling..." state ever gets a chance to
   * render. Keeping this true past that point keeps the button (and its
   * loading state) mounted for the whole window the effect below is
   * watching.
   *
   * Sourced from redux (persisted to localStorage), not local component
   * state: the Pipelines tab — and this button with it — unmounts on tab
   * switch, and everything resets on a full reload. Keeping the pending flag
   * outside the component is what lets "Cancelling..." still show (instead
   * of silently reverting to "Cancel" or "-") when the user comes back mid
   * cancellation.
   */
  const pendingCancellation = useAppSelector(
    selectPendingPipelineCancellation(run.project_id, run.id),
  );
  const isCancelPending = !!pendingCancellation;

  const taskType = getCancellableTaskType(run);
  const rowStatus = normalizeIngestionStatus(run.status);
  const isRunningNow = rowStatus === "running";
  const liveTask = useLatestProjectTask(run.project_id, taskType ?? TASK_TYPE.SOURCE_PROCESS);

  const isCancellable =
    !!taskType && isRunningNow && !!liveTask && !isTerminalTaskStatus(liveTask.status);

  /**
   * Confirm the cancel only once the run's own row says so.
   *
   * Deliberately keyed on `rowStatus` — the exact value the Status column
   * renders — and not on the live task going terminal. The backend flips the
   * ProjectTask to "cancelled" before it updates the ingestion row, so
   * confirming off the task announced "Pipeline run cancelled." while the
   * Status chip beside it still read Running. Whatever this toast claims, the
   * user is looking at that chip, so the chip is what it has to agree with.
   *
   * This also naturally handles a remount (tab switch back, or a reload)
   * that lands after the row has already stopped running while nobody was
   * watching: the effect runs on mount too, sees `isRunningNow` already
   * false, and clears the persisted entry immediately.
   */
  useEffect(() => {
    if (!pendingCancellation) return;
    if (isRunningNow) return;

    dispatch(clearPipelineCancellation({ projectId: run.project_id, runId: run.id }));
    // A run that stopped for some other reason (it finished, or failed, just
    // as the cancel landed) is not a cancellation — say nothing rather than
    // claim one.
    if (rowStatus === "cancelled") {
      toast.success("Pipeline run cancelled.");
    }
  }, [pendingCancellation, isRunningNow, rowStatus, dispatch, run.project_id, run.id]);

  // `isCancelPending` keeps the button/Modal mounted through the window
  // where `isCancellable` has already gone false but the row hasn't caught
  // up yet — see the state's doc comment above.
  if (!isCancellable && !isCancelPending) {
    return <span className="text-xs text-[var(--text-tertiary)]">-</span>;
  }

  const handleConfirmCancel = async () => {
    if (!liveTask) return;
    try {
      dispatch(
        trackPipelineCancellation({
          projectId: run.project_id,
          runId: run.id,
          taskId: liveTask.task_id,
        }),
      );
      await cancelTask({
        taskId: liveTask.task_id,
        projectId: run.project_id,
      }).unwrap();
      // The server cancels synchronously, so the run can already be stopped
      // (and confirmed by the effect above) before this resolves — the entry
      // is cleared in that case. Announcing "this may take a moment"
      // afterwards would contradict the confirmation the user just saw, so
      // only say it while the cancel really is still outstanding.
      const stillPending = selectPendingPipelineCancellation(
        run.project_id,
        run.id,
      )(store.getState());
      if (stillPending) {
        toast.info("Cancellation requested — this may take a moment to finish.");
      }
      setIsConfirming(false);
    } catch {
      dispatch(clearPipelineCancellation({ projectId: run.project_id, runId: run.id }));
      // Error toast is already shown by the shared RTK Query error handler.
    }
  };

  return (
    <>
      {isCancelPending ? (
        <span className="text-[13px] font-semibold text-[var(--text-tertiary)]">
          Cancelling...
        </span>
      ) : (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            setIsConfirming(true);
          }}
          className="inline-flex h-8 items-center justify-center rounded-lg border border-[var(--color-border-strong)] bg-transparent px-3 text-[13px] font-semibold text-[var(--error)] transition-colors duration-200 hover:bg-[var(--error)]/10"
        >
          Cancel
        </button>
      )}

      <Modal
        isOpen={isConfirming}
        onClose={() => !isLoading && !isCancelPending && setIsConfirming(false)}
        title="Cancel pipeline run"
        disableBackdropClose={isLoading || isCancelPending}
        disableEscClose={isLoading || isCancelPending}
        width={420}
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setIsConfirming(false)}
              disabled={isLoading || isCancelPending}
            >
              Keep running
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={handleConfirmCancel}
              loading={isLoading || isCancelPending}
              loadingText="Cancelling..."
            >
              Cancel run
            </Button>
          </>
        }
      >
        <p className="m-0 text-[length:var(--font-size-sm)] leading-6 text-[var(--color-neutral-400)]">
          Are you sure you want to cancel this pipeline run? This action cannot be undone.
        </p>
      </Modal>
    </>
  );
}
