// Shared contract v0. Coordinate field changes with backend/cqa/models.py.
export type Language = "RU" | "KZ" | "EN";
export type Speaker = "client" | "operator" | "bot" | "system";

export interface Message {
  message_id: string;
  timestamp: string;
  speaker: Speaker;
  text: string;
}

export interface Conversation {
  conversation_id: string;
  group_id: string;
  channel: "agent chat" | "voice call";
  language: Language;
  transcript: Message[];
  audio_url: string | null;
}

export interface Criterion {
  criterion_id: string;
  title: string;
  instruction: string;
  weight: number;
  answer_type: "boolean" | "scale" | "choice";
  na_allowed: boolean;
  requires_audio: boolean;
  scale_min: number | null;
  scale_max: number | null;
  higher_is_better: boolean;
  positive_answer: boolean;
  choices: Record<string, number>;
  section_id: string | null;
  gross_error_rule: string | null;
}

export interface ChecklistVersion {
  checklist_id: string;
  version: number;
  title: string;
  provenance: "synthetic_example" | "organizer";
  criteria: Criterion[];
}

export interface CriterionResult {
  criterion_id: string;
  status: "applicable" | "not_applicable" | "needs_review";
  answer: boolean | number | string | null;
  explanation: string;
  evidence: Array<{ message_id: string; quote: string }>;
  checked_span: { start_message_id: string; end_message_id: string } | null;
  gross_error: boolean;
}

export interface EvaluationDraft {
  conversation_id: string;
  group_id: string;
  checklist_id: string;
  checklist_version: number;
  prompt_version: string;
  results: CriterionResult[];
}
