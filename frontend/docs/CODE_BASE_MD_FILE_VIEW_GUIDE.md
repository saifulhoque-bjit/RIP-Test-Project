# Markdown File Viewer: Highlighting Requirements

## 1) Purpose
Define deterministic and user-safe highlighting behavior in the Markdown SRS viewer using:
- `acceptance_criteria[]`
- `srs_evidence[]`

This document is the implementation contract for locating and rendering evidence highlights.

## 2) Input Model and Field Usage

### 2.1 acceptance_criteria[] (AC)
Each AC object provides business-level behavior and traceability keys.

Required fields used by the viewer flow:
- `ac_code`: AC identifier (example: `SHARED-001-F1-S3-AC1`)
- `l2_source_ref`: evidence reference pointer (example: `SRS::MFU-001::API::GET_api_v1_auth_sessions_validate`)

### 2.2 srs_evidence[]
Each evidence object provides resolved SRS location and rendering hints.

Required fields used by the viewer flow:
- `l2_id`: must match AC `l2_source_ref` for resolved evidence
- `highlight_type`: one of `table_row`, `section`, `bullet_item`, `file`
- `line_number`: preferred deterministic locator when `> 0`
- `target_string`: fallback and section/bullet locator text
- `section_anchor`: parent section anchor (used by `bullet_item` and fallback contexts)
- `exact_quote`: display-only verbatim evidence text (never strict-matching source)
- `precision`: confidence hint (notably `file` case)
- `context_snippet`: optional contextual callout text (for low-precision handling)
- `ac_ids[]`: AC IDs linked to this evidence

## 3) AC to Evidence Resolution Flow

1. User selects an AC.
2. Read AC `l2_source_ref`.
3. If `l2_source_ref` starts with `GAP::`, apply GAP behavior (Section 5.2) and stop.
4. Find `srs_evidence` where `evidence.l2_id === ac.l2_source_ref`.
5. If no match exists, apply unresolved evidence behavior (Section 5.3) and stop.
6. Use the matched evidence `highlight_type` to choose highlight strategy (Section 4).
7. Render metadata panel details from evidence fields.
8. Optional consistency check: AC `ac_code` should exist in `evidence.ac_ids[]` when provided.

## 4) The Four Highlight Types (Step by Step)

### 4.1 highlight_type = table_row (most common)
Used for S.3 controls, S.4 validation rules, and S.5 events in UIBlueprint Markdown files.

Step 1: Navigate to the line.
- If `line_number > 0`:
	- Split file content by `\n`.
	- Jump to zero-indexed line: `line_number - 1`.
	- This is the preferred path (deterministic and instant).
- If `line_number = 0`:
	- Fall back to full-text search for `target_string`.
	- Scan lines top-to-bottom.
	- Match rule: case-insensitive contains.
	- Highlight the first matching line.

Step 2: Highlight the row.
- Apply yellow background `#FFF59D` to the entire line.
- Evidence unit is the full row, not only matched substring.
- `exact_quote` is display-only for Evidence Metadata Panel.
- Never use `exact_quote` for byte-exact matching (whitespace/unicode normalization may differ).
- Use `target_string` contains matching for location.

### 4.2 highlight_type = section (BatchSpec / JSON SRS)
Used for BatchSpec files where evidence maps to a whole processing-step section.

Step 1: Navigate to section heading.
- Use `target_string` to find the rendered heading in content.

Step 2: Highlight the block.
- Apply left-border highlight: `4px solid #FFA726`.
- Highlight full section block:
	- heading
	- all section body content
	- up to (but not including) the next section start
- This is intentionally broader than `table_row` highlight.

### 4.3 highlight_type = bullet_item (API Contracts)
Used for API endpoint entries in API contract documents.

Step 1: Find section.
- Locate parent section heading using `section_anchor` (example: `2.1 API Endpoint Strategy`).

Step 2: Find bullet within the section.
- Search line-by-line inside that section for `target_string`.
- Highlight matching bullet line with light yellow `#FFFDE7`.

Step 3: Fallback.
- If `target_string` is not found inside section:
	- Highlight entire section block.
	- Show indicator: `Approximate match`.

### 4.4 highlight_type = file (precision = file)
Lowest-confidence case where linker could not resolve a precise location.

Behavior:
- Render full SRS file with no specific in-body highlight.
- Show persistent top banner:
	- `Low Precision - exact location could not be determined.`
- If `context_snippet` is non-empty, show it in top callout box.
- Display `l2_source_ref` string for manual `Ctrl+F`.
- Do not apply any visual highlight to file body (to avoid misleading users).

## 5) Edge Cases

### 5.1 line_number = 0 (always fallback to text search)
This occurs when:
- BatchSpec JSON files do not map line numbers meaningfully
- linker run predates line-number indexing

Required behavior:
- Use case-insensitive contains search with `target_string`.
- Values are expected to be unique within section.
- If `target_string` is not found (document changed since indexing):
	- Highlight `section_anchor` heading block.
	- Notify user:
		- `Exact line not found - showing section context.`

### 5.2 GAP:: Reference (`l2_source_ref` starts with GAP::)
Meaning:
- Intentional documented absence of SRS error anchor for this AC.
- Not a system error.

Format:
- `GAP::{mfu_id}::{DESCRIPTION}`
- Example: `GAP::MFU-006::MISSING_ERROR_ANCHOR`

UI behavior:
- Do not open SRS file.
- Do not perform highlighting.
- Render AC `l2_source_ref` chip in amber with warning icon.
- Tooltip text on hover:
	- `No SRS error anchor exists for this AC. The GAP:: reference documents intentional SRS coverage absence.`
- SRS viewer remains on previously active evidence (or idle state).

### 5.3 No matching srs_evidence entry
Condition:
- AC `l2_source_ref` has no matching `l2_id` in `srs_evidence[]`.

Meaning:
- Evidence was not pre-resolved at generation time.

UI behavior:
- Show AC `l2_source_ref` as plain text with gray `?` badge.
- Show message:
	- `Evidence not pre-resolved for this reference. Inspect the SRS file manually.`
- Do not parse SRS file directly in browser (to avoid linker mismatch/inconsistent results).

### 5.4 exact_quote is empty
Rules:
- For `precision=file`, empty `exact_quote` is expected by design.
- For `precision=exact`, it should normally be populated.

If unexpectedly empty in a precise evidence case:
- Locate line using `target_string` (contains search) as normal.
- Highlight matching line.
- Omit `exact_quote` display field silently in metadata panel.
- Do not render an empty quote box.

## 6) Matching and Rendering Rules (Non-Negotiable)
- Location matching uses `target_string` with contains search (case-insensitive) whenever deterministic line mapping is unavailable.
- `exact_quote` is never used for strict matching logic.
- Top-to-bottom first-match policy applies for fallback line scans.
- Highlight unit depends on `highlight_type`:
	- `table_row`: full row
	- `section`: full section block
	- `bullet_item`: bullet line (or whole section as fallback)
	- `file`: no body highlight

## 7) Example Mapping from Provided Data
Given AC:
- `l2_source_ref = SRS::MFU-001::API::GET_api_v1_auth_sessions_validate`

Given evidence:
- `l2_id = SRS::MFU-001::API::GET_api_v1_auth_sessions_validate`
- `highlight_type = section`
- `target_string = GET /api/v1/auth/sessions/validate`

Expected behavior:
- Resolve AC to evidence by exact `l2_source_ref <-> l2_id` match.
- Execute `section` strategy:
	- find heading using `target_string`
	- apply `4px solid #FFA726` left-border highlight to entire section block.
