"""Shared input and evaluator contracts. Official scoring rules are configured separately."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RawMessage(Contract):
    timestamp: str = Field(min_length=1)
    speaker: Literal["client", "operator", "bot", "system"]
    text: str


class Message(RawMessage):
    message_id: str = Field(min_length=1)


class RawConversation(Contract):
    conversation_id: str = Field(min_length=1)
    channel: Literal["agent chat", "voice call"]
    language: Literal["RU", "KZ", "EN"]
    transcript: list[RawMessage] = Field(min_length=1)
    audio_url: str | None = None

    @field_validator("conversation_id")
    @classmethod
    def meaningful_id(cls, value):
        if not value.strip():
            raise ValueError("conversation_id must not be blank")
        return value

    @field_validator("transcript")
    @classmethod
    def meaningful_transcript(cls, value):
        if not any(message.text.strip() for message in value):
            raise ValueError("transcript must contain at least one non-blank message")
        return value


class Conversation(RawConversation):
    group_id: str = Field(min_length=1)
    transcript: list[Message] = Field(min_length=1)

    @field_validator("group_id")
    @classmethod
    def meaningful_group_id(cls, value):
        if not value.strip():
            raise ValueError("group_id must not be blank")
        return value


class Dataset(Contract):
    dataset_group_id: str = Field(min_length=1)
    total_records: int = Field(ge=0)
    records: list[RawConversation]

    @field_validator("dataset_group_id")
    @classmethod
    def meaningful_group_id(cls, value):
        if not value.strip():
            raise ValueError("dataset_group_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_records(self):
        ids = [record.conversation_id for record in self.records]
        if self.total_records != len(ids):
            raise ValueError("total_records does not match the records array")
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate conversation_id inside a dataset group")
        return self


class DatasetExport(Contract):
    error: str = ""
    isSuccess: Literal[True]
    data: Dataset


class Criterion(Contract):
    criterion_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    weight: float = Field(ge=0)
    answer_type: Literal["boolean", "scale", "choice"]
    na_allowed: bool = False
    requires_audio: bool = False
    scale_min: float | None = None
    scale_max: float | None = None
    allowed_values: list[float] | None = None
    higher_is_better: bool = True
    positive_answer: bool = True
    choices: dict[str, float] = Field(default_factory=dict)
    section_id: str | None = None
    gross_error_rule: str | None = None
    critical_answer: bool | None = None

    @model_validator(mode="after")
    def validate_answer_type(self):
        if self.answer_type == "scale":
            if self.scale_min is None or self.scale_max is None or self.scale_min >= self.scale_max:
                raise ValueError("A scale requires ordered finite bounds")
        if self.allowed_values is not None:
            if self.answer_type != "scale" or not self.allowed_values:
                raise ValueError("Discrete values require a non-empty numeric scale")
            if len(set(self.allowed_values)) != len(self.allowed_values) or any(
                not self.scale_min <= value <= self.scale_max for value in self.allowed_values
            ):
                raise ValueError("Discrete scale values must be unique and within bounds")
        if self.critical_answer is not None and (
            self.answer_type != "boolean" or not self.gross_error_rule or not self.gross_error_rule.strip()
        ):
            raise ValueError("A critical answer requires a boolean criterion and a gross-error rule")
        if self.answer_type == "choice":
            if not self.choices or any(not 0 <= score <= 1 for score in self.choices.values()):
                raise ValueError("Choices require normalized scores between 0 and 1")
        return self


class ChecklistVersion(Contract):
    checklist_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    title: str = Field(min_length=1)
    provenance: Literal["synthetic_example", "organizer"]
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ids(self):
        ids = [criterion.criterion_id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate criterion_id in one checklist version")
        return self


class Evidence(Contract):
    message_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class CheckedSpan(Contract):
    start_message_id: str = Field(min_length=1)
    end_message_id: str = Field(min_length=1)


class CriterionResult(Contract):
    criterion_id: str = Field(min_length=1)
    status: Literal["applicable", "not_applicable", "needs_review"]
    answer: bool | float | str | None
    explanation: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)
    checked_span: CheckedSpan | None = None
    gross_error: bool = False

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == "applicable":
            if self.answer is None or (not self.evidence and self.checked_span is None):
                raise ValueError("Applicable results need an answer and evidence or a checked span")
        elif self.answer is not None:
            raise ValueError("N/A and needs_review require a null answer")
        return self


class EvaluationDraft(Contract):
    conversation_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    checklist_id: str = Field(min_length=1)
    checklist_version: int = Field(ge=1)
    prompt_version: str = Field(min_length=1)
    results: list[CriterionResult] = Field(min_length=1)
