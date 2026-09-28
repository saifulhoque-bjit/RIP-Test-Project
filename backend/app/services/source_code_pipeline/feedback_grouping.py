from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from app.utils.logger import get_logger

    logger = get_logger(__name__)
except ImportError:
    import logging

    logger = logging.getLogger(__name__)


class BasketFeedbackTarget(BaseModel):
    """Target metadata for a single feedback item."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    module_id: str | None = None
    mfu_id: str | None = None
    user_story_id: str | None = None

    @field_validator("project_id", "module_id", "mfu_id", "user_story_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class BasketFeedbackContent(BaseModel):
    """Natural-language feedback content wrapper."""

    model_config = ConfigDict(extra="ignore")

    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content must not be empty")
        return cleaned


class BasketSpecificFeedbackItem(BaseModel):
    """Inline selected-text feedback fragment."""

    model_config = ConfigDict(extra="ignore")

    selected_text: str = Field(min_length=1)
    content: str = Field(min_length=1)

    @field_validator("selected_text", "content")
    @classmethod
    def _validate_text_fields(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text fields must not be empty")
        return cleaned


class BasketFeedbackItemRequest(BaseModel):
    """Single incoming basket-feedback item."""

    model_config = ConfigDict(extra="ignore")

    target: BasketFeedbackTarget
    overall_feedback: BasketFeedbackContent | None = None
    specific_feedback: list[BasketSpecificFeedbackItem] = Field(default_factory=list)


class BasketFeedbackRequest(BaseModel):
    """Incoming stacked feedback request payload."""

    model_config = ConfigDict(extra="ignore")

    feedbacks: list[BasketFeedbackItemRequest] = Field(min_length=1)


class FeatureScopedFeedback(BaseModel):
    """Feature-level or user-story-level feedback entry in grouped response."""

    model_config = ConfigDict(extra="ignore")

    scope: Literal["FEATURE", "USER_STORY"]
    user_story_id: str | None = None
    overall_feedback: BasketFeedbackContent | None = None
    specific_feedback: list[BasketSpecificFeedbackItem] = Field(default_factory=list)


class FeatureWiseFeedbackBucket(BaseModel):
    """Grouped feedback bucket keyed by project/module/mfu."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    module_id: str
    mfu_id: str
    feedbacks: list[FeatureScopedFeedback] = Field(default_factory=list)


class FeatureWiseFeedbackResponse(BaseModel):
    """Feature-wise grouped feedback response payload."""

    model_config = ConfigDict(extra="ignore")

    features: list[FeatureWiseFeedbackBucket] = Field(default_factory=list)


class FeedbackGroupingService:
    """Pure utility service for validating and grouping stacked feedback."""

    @staticmethod
    def _normalize_feedback_content(value: Any) -> dict[str, str] | None:
        """Normalize feedback content into legacy {content: str} shape."""
        if isinstance(value, dict):
            value = value.get("content", "")
        cleaned = str(value or "").strip()
        if not cleaned:
            return None
        return {"content": cleaned}

    @staticmethod
    def _normalize_feedback_request_payload(
        basket_feedback_request: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Normalize incoming payload into legacy request model shape.

        Accepted shapes:
        - {"feedbacks": [{"target": ..., "overall_feedback": {"content": ...}, ...}]}
        - {"feedbacks": [{"module_id": ..., "mfu_id": ..., "overall_feedback": "...", ...}]}
        - [{...}, {...}]  (raw feedback list)
        """
        request_obj = basket_feedback_request
        if isinstance(request_obj, list):
            request_obj = {"feedbacks": request_obj}

        if not isinstance(request_obj, dict):
            return {"feedbacks": []}

        incoming_items = request_obj.get("feedbacks")
        if not isinstance(incoming_items, list):
            return {"feedbacks": []}

        default_project_id = (
            str(request_obj.get("project_id") or "UNKNOWN_PROJECT").strip() or "UNKNOWN_PROJECT"
        )
        normalized_items: list[dict[str, Any]] = []

        for item in incoming_items:
            if not isinstance(item, dict):
                continue

            if isinstance(item.get("target"), dict):
                normalized_items.append(item)
                continue

            target = {
                "project_id": str(item.get("project_id") or default_project_id).strip()
                or default_project_id,
                "module_id": str(item.get("module_id") or "").strip() or None,
                "mfu_id": str(item.get("mfu_id") or "").strip() or None,
                "user_story_id": str(item.get("user_story_id") or "").strip() or None,
            }

            overall_feedback = FeedbackGroupingService._normalize_feedback_content(
                item.get("overall_feedback")
            )

            normalized_specifics: list[dict[str, str]] = []
            raw_specific_feedback = item.get("specific_feedback")
            if isinstance(raw_specific_feedback, list):
                for specific in raw_specific_feedback:
                    if not isinstance(specific, dict):
                        continue
                    selected_text = str(specific.get("selected_text") or "").strip()
                    content = str(
                        specific.get("selected_feedback", specific.get("content", "")) or ""
                    ).strip()
                    if selected_text and content:
                        normalized_specifics.append(
                            {"selected_text": selected_text, "content": content}
                        )

            if not overall_feedback and not normalized_specifics:
                continue

            normalized_item: dict[str, Any] = {
                "target": target,
                "specific_feedback": normalized_specifics,
            }
            if overall_feedback:
                normalized_item["overall_feedback"] = overall_feedback

            normalized_items.append(normalized_item)

        return {"feedbacks": normalized_items}

    @staticmethod
    def group_feedbacks_feature_wise(
        basket_feedback_request: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and group stacked feedback into feature-wise buckets."""
        normalized_payload = FeedbackGroupingService._normalize_feedback_request_payload(
            basket_feedback_request
        )
        request = BasketFeedbackRequest.model_validate(normalized_payload)

        grouped: dict[tuple[str, str, str], FeatureWiseFeedbackBucket] = {}
        for feedback in request.feedbacks:
            project_id = feedback.target.project_id
            module_id = feedback.target.module_id
            mfu_id = feedback.target.mfu_id

            if not module_id or not mfu_id:
                raise ValueError(
                    "module_id and mfu_id are required for feature-wise feedback grouping"
                )

            bucket_key = (project_id, module_id, mfu_id)
            bucket = grouped.get(bucket_key)
            if bucket is None:
                bucket = FeatureWiseFeedbackBucket(
                    project_id=project_id,
                    module_id=module_id,
                    mfu_id=mfu_id,
                    feedbacks=[],
                )
                grouped[bucket_key] = bucket

            is_story_feedback = bool(feedback.target.user_story_id)
            bucket.feedbacks.append(
                FeatureScopedFeedback(
                    scope="USER_STORY" if is_story_feedback else "FEATURE",
                    user_story_id=feedback.target.user_story_id if is_story_feedback else None,
                    overall_feedback=feedback.overall_feedback,
                    specific_feedback=feedback.specific_feedback,
                )
            )

        response = FeatureWiseFeedbackResponse(features=list(grouped.values()))
        logger.info(
            "[PIPELINE] Grouped stacked feedback into %d feature bucket(s) from %d item(s)",
            len(response.features),
            len(request.feedbacks),
        )
        return response.model_dump(mode="json")

    @staticmethod
    def compose_revise_feedback_from_feature_bucket(
        feature_bucket: dict[str, Any],
    ) -> str:
        """Flatten a grouped feature feedback bucket into a revise-ready feedback string."""
        validated_bucket = FeatureWiseFeedbackBucket.model_validate(feature_bucket)

        lines: list[str] = []
        lines.append(
            "Consolidated reviewer feedback for "
            f"module={validated_bucket.module_id}, mfu={validated_bucket.mfu_id}:"
        )

        for index, feedback in enumerate(validated_bucket.feedbacks, start=1):
            if feedback.scope == "USER_STORY" and feedback.user_story_id:
                header = f"{index}. [USER_STORY:{feedback.user_story_id}]"
            else:
                header = f"{index}. [FEATURE]"

            if feedback.overall_feedback and feedback.overall_feedback.content:
                lines.append(f"{header} {feedback.overall_feedback.content}")

            for sf_index, specific in enumerate(feedback.specific_feedback, start=1):
                lines.append(
                    f'   {index}.{sf_index} selected_text="{specific.selected_text}" | '
                    f"feedback={specific.content}"
                )

        return "\n".join(lines)

    @staticmethod
    def compose_revise_edit_payload_from_feature_bucket(
        feature_bucket: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build edit-mode revise payload fields for run_for_mfu/cmd_revise.

        Returns:
            {
                "feedback": str,
                "quoted_text": str | None,
                "target_ids": list[str],
            }
        """
        validated_bucket = FeatureWiseFeedbackBucket.model_validate(feature_bucket)

        feedback_lines: list[str] = []
        quoted_segments: list[str] = []
        target_ids: list[str] = []

        for feedback in validated_bucket.feedbacks:
            if feedback.scope == "USER_STORY" and feedback.user_story_id:
                scope_tag = f"[USER_STORY:{feedback.user_story_id}]"
                target_ids.append(feedback.user_story_id)
            else:
                scope_tag = "[FEATURE]"
                target_ids.append("")  # Feature-level placeholder — no specific story targeted

            if feedback.overall_feedback and feedback.overall_feedback.content:
                feedback_lines.append(f"{scope_tag} {feedback.overall_feedback.content}")

            for specific in feedback.specific_feedback:
                if specific.selected_text:
                    quoted_segments.append(specific.selected_text)
                feedback_lines.append(
                    f'{scope_tag} For selected text "{specific.selected_text}": {specific.content}'
                )

        deduped_target_ids = list(dict.fromkeys(target_ids))  # preserves "", dedupes story IDs
        deduped_quoted_segments = list(dict.fromkeys([qt for qt in quoted_segments if qt]))

        return {
            "feedback": "\n".join(feedback_lines).strip(),
            "quoted_text": "\n".join(deduped_quoted_segments).strip() or None,
            "target_ids": deduped_target_ids,
        }

    @staticmethod
    def compose_revise_feedback_spec_from_feature_bucket(
        feature_bucket: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build structured revise feedback_spec payload expected by cmd_revise.

        Returns:
            {
                "module": str | None,
                "mfu": str | None,
                "entries": [
                    {
                        "story_id": str | None,
                        "items": [
                            {"quoted_text": str | None, "feedback_text": str}
                        ]
                    }
                ]
            }
        """
        validated_bucket = FeatureWiseFeedbackBucket.model_validate(feature_bucket)

        entries_map: dict[str | None, list[dict[str, str | None]]] = {}

        for feedback in validated_bucket.feedbacks:
            story_id: str | None = (
                feedback.user_story_id
                if feedback.scope == "USER_STORY" and feedback.user_story_id
                else None
            )

            entries_map.setdefault(story_id, [])

            if feedback.overall_feedback and feedback.overall_feedback.content:
                entries_map[story_id].append(
                    {
                        "quoted_text": None,
                        "feedback_text": feedback.overall_feedback.content,
                    }
                )

            for specific in feedback.specific_feedback:
                feedback_text = str(specific.content or "").strip()
                if not feedback_text:
                    continue
                quoted_text = str(specific.selected_text or "").strip() or None
                entries_map[story_id].append(
                    {
                        "quoted_text": quoted_text,
                        "feedback_text": feedback_text,
                    }
                )

        entries: list[dict[str, Any]] = []
        for story_id, items in entries_map.items():
            if not items:
                continue
            entries.append({"story_id": story_id, "items": items})

        return {
            "module": validated_bucket.module_id,
            "mfu": validated_bucket.mfu_id,
            "entries": entries,
        }
