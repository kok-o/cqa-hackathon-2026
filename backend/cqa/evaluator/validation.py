"""Validate a model draft before it can be used for scoring."""

from ..models import ChecklistVersion, Conversation, EvaluationDraft


def validate_draft(
    conversation: Conversation, checklist: ChecklistVersion, draft: EvaluationDraft,
) -> None:
    if (draft.conversation_id, draft.group_id) != (conversation.conversation_id, conversation.group_id):
        raise ValueError("Draft belongs to another conversation")
    if (draft.checklist_id, draft.checklist_version) != (checklist.checklist_id, checklist.version):
        raise ValueError("Draft belongs to another checklist version")
    criteria = {item.criterion_id: item for item in checklist.criteria}
    result_ids = [result.criterion_id for result in draft.results]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(criteria):
        raise ValueError("Expected exactly one result per criterion")

    messages = {message.message_id: message for message in conversation.transcript}
    positions = {message.message_id: index for index, message in enumerate(conversation.transcript)}
    for result in draft.results:
        criterion = criteria[result.criterion_id]
        if not result.explanation.strip():
            raise ValueError("A result requires a non-empty explanation")
        if result.gross_error and (not criterion.gross_error_rule or not criterion.gross_error_rule.strip()):
            raise ValueError("Gross error requires a configured rule")
        if result.gross_error and result.status != "applicable":
            raise ValueError("Unresolved and N/A results cannot establish a gross error")
        if result.status == "not_applicable" and not criterion.na_allowed:
            raise ValueError(f"N/A is not allowed for {criterion.criterion_id}")
        if result.status == "applicable":
            answer = result.answer
            if criterion.answer_type == "boolean" and type(answer) is not bool:
                raise ValueError("Expected a boolean answer")
            if criterion.answer_type == "scale":
                if type(answer) not in (float, int) or not criterion.scale_min <= answer <= criterion.scale_max:
                    raise ValueError("Answer outside the configured scale")
            if criterion.answer_type == "choice" and answer not in criterion.choices:
                raise ValueError("Answer is not a configured choice")
        for evidence in result.evidence:
            message = messages.get(evidence.message_id)
            if message is None or not evidence.quote.strip() or evidence.quote not in message.text:
                raise ValueError("Evidence must point to an exact source quote")
        if result.checked_span:
            start = positions.get(result.checked_span.start_message_id)
            end = positions.get(result.checked_span.end_message_id)
            if start is None or end is None or start > end:
                raise ValueError("Invalid checked message span")
