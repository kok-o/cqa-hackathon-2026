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
  allowed_values?: number[] | null;
  higher_is_better: boolean;
  positive_answer: boolean;
  choices: Record<string, number>;
  section_id: string | null;
  gross_error_rule: string | null;
  critical_answer?: boolean | null;
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

export interface ConversationSummary {
  conversation_id: string;
  group_id: string;
  channel: Conversation['channel'];
  language: Language;
  message_count: number;
  preview: string;
  latest_evaluation_id: string | null;
  latest_score: number | null;
  latest_status: string | null;
  latest_gross_error?: boolean | null;
}

export interface ProviderInfo {
  provider_configured: boolean;
  provider: string;
  model: string | null;
  mode: string;
  prompt_version: string;
  team_name?: string;
}

export interface SubmissionFile {
  schema_version: '1.0';
  team_name: string;
  model: { mode: 'local' | 'external_api' | 'hybrid'; name: string };
  results: Array<{ conversation_id: string; status: 'ok' | 'error'; criteria: unknown[]; score: { final_score: number; critical_flag: boolean } | null; error?: string }>;
}

export interface Score {
  total: number | null;
  status: 'complete' | 'incomplete' | 'not_applicable';
  coverage: number;
  earned_weight: number;
  applicable_weight: number;
  criteria: Array<{ criterion_id: string; status: string; normalized_score: number | null; weight: number; contribution: number | null }>;
  sections: Record<string, number | null>;
  gross_error: boolean;
}

export interface Appeal {
  appeal_id: string;
  evaluation_id: string;
  criterion_id: string;
  comment: string;
  status: 'open' | 'resolved';
  created_at: string;
  decision: null | { action: 'uphold' | 'replace'; reason: string; replacement_result: CriterionResult | null; created_at: string };
}

export interface Evaluation {
  evaluation_id: string;
  conversation: Conversation;
  checklist: ChecklistVersion;
  original_results: CriterionResult[];
  results: CriterionResult[];
  original_score: Score;
  score: Score;
  model_info: Record<string, unknown>;
  revision: number;
  appeals: Appeal[];
  created_at: string;
}

export interface EvaluationJob {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  error: string | null;
  evaluation_id: string | null;
  error_code?: string | null;
  model_info?: Record<string, unknown> | null;
}
