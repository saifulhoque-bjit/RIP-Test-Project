const TYPE_LABEL: Record<string, string> = {
  rfp: "RFP",
  source_code: "SOURCE CODE",
  requirement_update: "FEEDBACK",
  requirement_update_from_feedback: "FEEDBACK",
  meeting_notes: "MEETING NOTES",
  additional_rfp: "ADDITIONAL RFP",
};

/** Human label for a source_type value — shared by the Sources and Pipelines tables' Type columns so the two always agree. */
export function getTypeLabel(sourceType: string): string {
  return TYPE_LABEL[sourceType] ?? sourceType.toUpperCase();
}
