import type { ChangeType } from "@/types/user-story";

export interface ChangeTypeCarrier {
  incremental_change_type?: ChangeType;
  feedback_change_type?: ChangeType;
}

// A pending module/feature/story change surfaces via either signal —
// incremental_change_type (AI-driven regeneration) or feedback_change_type
// (user feedback-driven regeneration) — and either can be populated on its
// own, so every consumer must check both rather than incremental_change_type
// alone.
export const getEffectiveChangeType = (
  item?: ChangeTypeCarrier | null,
): ChangeType => item?.incremental_change_type || item?.feedback_change_type || null;
