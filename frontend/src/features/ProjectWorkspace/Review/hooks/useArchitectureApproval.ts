import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "@/lib/toast";
import { getErrorMessage } from "@/utils/getErrorMessage";
import { useAppDispatch } from "@/store/hooks";
import { baseApi } from "@/services/api/baseApi";
import { useApproveModulesMutation } from "@/services/api/modules/modules";

/** Owns the "Approve architecture & generate user stories" modal + mutation for RFP Stage 1. */
export function useArchitectureApproval(projectId?: string) {
  const navigate = useNavigate();
  const dispatch = useAppDispatch();
  const [approveModules, { isLoading: isApproving }] =
    useApproveModulesMutation();
  const [isModalOpen, setIsModalOpen] = useState(false);

  const openModal = () => setIsModalOpen(true);

  const closeModal = () => {
    if (!isApproving) {
      setIsModalOpen(false);
    }
  };

  const handleConfirm = async (skipProcessing?: boolean) => {
    if (!projectId) return;

    try {
      const result = await approveModules({ projectId, skipProcessing }).unwrap();

      toast.success(result.message || "Architecture approved successfully.");
      setIsModalOpen(false);
      // Mutation has no invalidatesTags (relies on WS terminal-status events),
      // so force the Pipelines table to refetch now rather than showing the
      // pre-approval snapshot until the next reload.
      dispatch(
        baseApi.util.invalidateTags([
          { type: "IngestionJob", id: `LIST-${projectId}` },
        ]),
      );
      navigate(`/projects/${projectId}/pipelines`);
    } catch (error) {
      toast.error(
        getErrorMessage(
          error,
          "Failed to approve architecture. Please try again.",
        ),
      );
    }
  };

  return {
    isModalOpen,
    openModal,
    closeModal,
    isApproving,
    handleConfirm,
  };
}
