"""Validate a model draft before it can be used for scoring."""

from ..models import ChecklistVersion, Conversation, EvaluationDraft


class DraftValidationError(ValueError):
    """A stable failure code and server-validated identifiers, without model text."""

    def __init__(self, message: str, *, code: str, path: tuple[str | int, ...] = (),
                 criterion_id: str | None = None, message_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.path = path
        self.criterion_id = criterion_id
        self.message_id = message_id


def validate_draft(
    conversation: Conversation, checklist: ChecklistVersion, draft: EvaluationDraft,
) -> None:
    if (draft.conversation_id, draft.group_id) != (conversation.conversation_id, conversation.group_id):
        raise DraftValidationError("Draft belongs to another conversation", code="conversation_mismatch")
    if (draft.checklist_id, draft.checklist_version) != (checklist.checklist_id, checklist.version):
        raise DraftValidationError("Draft belongs to another checklist version", code="checklist_mismatch")
    criteria = {item.criterion_id: item for item in checklist.criteria}
    result_ids = [result.criterion_id for result in draft.results]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(criteria):
        raise DraftValidationError("Expected exactly one result per criterion", code="criterion_set_mismatch", path=("results",))

    messages = {message.message_id: message for message in conversation.transcript}
    positions = {message.message_id: index for index, message in enumerate(conversation.transcript)}
    for result_index, result in enumerate(draft.results):
        criterion = criteria[result.criterion_id]

        def invalid(code: str, message: str, *field_path: str | int, message_id: str | None = None) -> DraftValidationError:
            return DraftValidationError(message, code=code, path=("results", result_index, *field_path),
                criterion_id=criterion.criterion_id, message_id=message_id if message_id in messages else None)

        if not result.explanation.strip():
            raise invalid("explanation_empty", "A result requires a non-empty explanation", "explanation")
        if result.gross_error and (not criterion.gross_error_rule or not criterion.gross_error_rule.strip()):
            raise invalid("gross_error_rule_missing", "Gross error requires a configured rule", "gross_error")
        if result.gross_error and result.status != "applicable":
            raise invalid("gross_error_status_invalid", "Unresolved and N/A results cannot establish a gross error", "gross_error")
        if result.status == "not_applicable" and not criterion.na_allowed:
            raise invalid("na_not_allowed", "N/A is not allowed for this criterion", "status")
        if result.status == "applicable":
            answer = result.answer
            if criterion.answer_type == "boolean" and type(answer) is not bool:
                raise invalid("answer_type", "Expected a boolean answer", "answer")
            if criterion.answer_type == "scale":
                if type(answer) not in (float, int) or not criterion.scale_min <= answer <= criterion.scale_max:
                    raise invalid("scale_out_of_bounds", "Answer outside the configured scale", "answer")
                if criterion.allowed_values is not None and answer not in criterion.allowed_values:
                    raise invalid("discrete_value_invalid", "Answer must be one of the configured allowed_values", "answer")
            if criterion.answer_type == "choice" and answer not in criterion.choices:
                raise invalid("choice_value_invalid", "Answer is not a configured choice", "answer")
            if criterion.critical_answer is not None and result.gross_error != (answer == criterion.critical_answer):
                raise invalid("critical_flag_mismatch", "Gross-error flag must match the configured critical answer", "gross_error")
        for evidence_index, evidence in enumerate(result.evidence):
            message = messages.get(evidence.message_id)
            if message is None:
                raise invalid("evidence_message_unknown", "Evidence must point to an exact source quote", "evidence", evidence_index, "message_id")
            if not evidence.quote.strip() or evidence.quote not in message.text:
                raise invalid("quote_not_exact", "Evidence must point to an exact source quote", "evidence", evidence_index, "quote", message_id=message.message_id)
        if result.checked_span:
            start = positions.get(result.checked_span.start_message_id)
            end = positions.get(result.checked_span.end_message_id)
            if start is None or end is None or start > end:
                raise invalid("checked_span_invalid", "Invalid checked message span", "checked_span")
