# ReviewGuidanceMessage

A contextual banner component rendered inside **UserStoryDetails** that communicates the AI critic result for the parent feature to the human reviewer. The banner tone, title, message, and expandable detail sections are all driven by `generation_metadata` fetched from the Feature Details API.

---

## Location

```
src/features/Review/DetailsComponents/ReviewGuidanceMessage.tsx
```

Used exclusively in:

```
src/features/Review/DetailsComponents/UserStoryDetails.tsx
```

---

## Props

| Prop | Type | Required | Description |
|---|---|---|---|
| `generationMetadata` | `FeatureGenerationMetadata \| null` | recommended | Full metadata object from the Feature Details API response (`data.feature.generation_metadata`). Drives all banner content. |
| `status` | `string \| null` | fallback only | Legacy plain-string status. Used only when `generationMetadata` is unavailable. |

> **Priority:** `generationMetadata.final_status` is always preferred over the `status` string prop.

---

## Data Flow

```
Feature Details API
  └─ data.feature.generation_metadata
        ├─ final_status          → selects banner tone + message
        ├─ relaxed_gates[]       → shown for PASS_RELAXED_GATES
        ├─ gate_profile          → shown for PASS_RELAXED_GATES
        ├─ fallback_reason       → shown for PASS_DETERMINISTIC_FALLBACK
        ├─ failure_reason        → shown for FAIL_HALLUCINATION
        └─ review_guidance
              ├─ approve_as_is_allowed → splits FAIL_CORRECTION_EXHAUSTED into trivial/substantive
              ├─ failure_summary       → collapsible detail for FAIL_CORRECTION_EXHAUSTED
              ├─ gate_reference[]      → gate name + meaning list
              └─ flagged_story_ids[]   → story ID chips
```

### Auto-fetch when story is selected directly

When the user clicks a **user story** node in the tree (bypassing the feature node), the container (`UserStory.tsx`) automatically detects the parent feature via `storyParentFeatureId` and fires `useGetProjectFeatureDetailQuery` to retrieve its `generation_metadata`. The banner therefore always has data regardless of navigation path.

---

## Status → UX Behaviour

| `final_status` | Tone | Title | Approve allowed | Extra detail shown |
|---|---|---|---|---|
| `PASS` | 🟢 green | Ready · critic PASS | ✅ yes | — |
| `PASS_DETERMINISTIC_FALLBACK` | 🟡 amber | AI analysis unavailable — deterministic scaffold | ✅ yes (spot-check recommended) | `fallback_reason` |
| `PASS_RELAXED_GATES` | 🟡 amber | PASS · relaxed gates | ✅ yes (spot-check recommended) | `relaxed_gates[]` chips + `gate_profile` |
| `FAIL_CORRECTION_EXHAUSTED` (trivial, `approve_as_is_allowed: true`) | 🟡 amber | Correction exhausted · trivial (controls only) | ✅ yes | — |
| `FAIL_CORRECTION_EXHAUSTED` (substantive, `approve_as_is_allowed: false`) | 🔵 blue/orange | Correction exhausted | ⛔ blocked | Collapsible `failure_summary` + `gate_reference[]` |
| `FAIL_HALLUCINATION` | 🔴 red | Hallucination — not grounded in the SRS | ⛔ hard-blocked | Collapsible `failure_reason` |
| `FAIL_SCHEMA_INVALID` | 🔴 red | Schema invalid — quarantined | ⛔ hard-blocked (no override) | — |
| `FAIL_PARSE_ERROR` | 🔵 blue | Transient generation error | ⛔ no approve | — |
| `FAIL_MAX_RETRIES` | 🔴 red | No usable result — max retries reached | ⛔ no approve | — |
| `FAIL_NO_PARSEABLE_OUTPUT` | 🔴 red | No usable result — no parseable output | ⛔ no approve | — |
| `FAIL_CORRECTOR_NO_PROGRESS` | 🔴 red | No usable result — corrector stalled | ⛔ no approve | — |
| unknown / missing | — | *(banner hidden — `null` returned)* | — | — |

---

## Tone → CSS Classes

| Tone | Border | Background | Use case |
|---|---|---|---|
| `ok` | `#a9e2cd` | `var(--success-50)` | All-pass, safe to approve |
| `low` | `#f0d69a` | `var(--warn-50)` | Pass with caveats, spot-check |
| `sub` | `#bcd8f7` | `var(--info-50)` | Substantive fail or transient error |
| `crit` | `#f3b4b4` | `var(--error-50)` | Critical fail, approve blocked |

---

## FAIL_CORRECTION_EXHAUSTED Split Logic

This status has two distinct UX paths determined at render time by `review_guidance.approve_as_is_allowed`:

```ts
const isExhaustedTrivial =
    normalized === "FAIL_CORRECTION_EXHAUSTED" &&
    generationMetadata?.review_guidance?.approve_as_is_allowed === true;
```

| `approve_as_is_allowed` | Variant | Tone | Banner title |
|---|---|---|---|
| `true` | Trivial — controls only (Gate 7c noise) | amber (`low`) | Correction exhausted · trivial (controls only) |
| `false` | Substantive — gates failed | blue/orange (`sub`) | Correction exhausted |

---

## Expandable Detail Sections

All collapsible sections use a native `<details>/<summary>` element — no JS state needed.

| Section | Condition |
|---|---|
| **Relaxed gates chips** | `PASS_RELAXED_GATES` + `relaxed_gates[]` non-empty |
| **Gate profile label** | `PASS_RELAXED_GATES` + `gate_profile` present |
| **Fallback reason** | `PASS_DETERMINISTIC_FALLBACK` + `fallback_reason` present |
| **Failure detail** (collapsible) | `FAIL_HALLUCINATION` + `failure_reason` present |
| **Failure summary** (collapsible) | `FAIL_CORRECTION_EXHAUSTED` (substantive) + `rg.failure_summary` present |
| **Gate reference list** | `rg.gate_reference[]` non-empty (any status) |
| **Flagged story IDs** | `rg.flagged_story_ids[]` non-empty (any status) |

---

## Usage Example

```tsx
// Driven by parent feature metadata (standard path)
<ReviewGuidanceMessage generationMetadata={featureDetail.generation_metadata} />

// Legacy plain-status path (backward compat only)
<ReviewGuidanceMessage status="PASS" />
```

---

## Related Types

```
src/types/feature.tsx
  FeatureGenerationMetadata
  FeatureReviewGuidance
  FeatureGuidanceGateReference
```

---

## Related Files

| File | Role |
|---|---|
| [ReviewGuidanceMessage.tsx](../src/features/Review/DetailsComponents/ReviewGuidanceMessage.tsx) | Component source |
| [UserStoryDetails.tsx](../src/features/Review/DetailsComponents/UserStoryDetails.tsx) | Only consumer — receives `featureGenerationMetadata` prop |
| [UserStory.tsx](../src/features/Review/UserStory/UserStory.tsx) | Fetches feature detail; derives parent feature id for direct-story navigation; passes `featureDetail.generation_metadata` down |
| [features.ts (API module)](../src/services/api/modules/features.ts) | `useGetProjectFeatureDetailQuery` — RTK Query hook |
| [feature.tsx (types)](../src/types/feature.tsx) | Type definitions for generation metadata |
