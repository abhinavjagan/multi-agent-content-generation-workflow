// Mirrors src/x_agent/server.py response models. Keep in sync.

export type PostMode = "single" | "thread";
export type QuestionKind = "open" | "generative";
export type ApproveAction = "approve" | "edit" | "regenerate" | "reject";

export interface OllamaStatus {
  ok: boolean;
  base_url: string;
  configured_model: string;
  embedding_model: string;
  critic_model: string;
  has_configured_model: boolean;
  available_models: string[];
  error: string | null;
}

export interface PersonaStats {
  count: number;
  dir: string;
}

export interface ResearchConfig {
  /** User preference: "auto" | "ddg" | "tavily" | "brave". */
  preference: string;
  /** Provider that will actually run given the configured keys. */
  active_provider: string;
  has_tavily_key: boolean;
  has_brave_key: boolean;
  max_results: number;
  fetch_timeout_s: number;
  max_content_chars: number;
}

export interface AppConfig {
  max_tweet_chars: number;
  critic_min_score: number;
  critic_max_attempts: number;
  persona_top_k: number;
  research: ResearchConfig;
}

export interface Health {
  version: string;
  ollama: OllamaStatus;
  personas: PersonaStats;
  config: AppConfig;
}

/**
 * A single source the agent saw -- either a search hit (with a snippet)
 * or a directly-fetched URL (with extracted article text). The UI shows
 * these as expandable cards under draft / variant results.
 */
export interface WebResult {
  url: string;
  title: string;
  snippet: string;
  content: string;
  source: "search" | "fetched";
  provider: string | null;
  score: number | null;
}

export interface DraftRequest {
  topic: string;
  mode: PostMode;
  style?: string;
  model?: string | null;
  persona_id?: string | null;
  /**
   * Optional pre-generated posts. When set, the backend skips the LLM
   * generation step and treats these as the draft, then runs the critic +
   * HITL review as usual. This is how "Pick this variant" hands a chosen
   * variant into the normal review pipeline.
   */
  seed_posts?: string[];
  /**
   * Web research opt-in. When true, the agent fetches `research_urls`
   * (if any) or searches for `research_query` (defaults to topic) and
   * grounds the draft in the extracted text.
   */
  research_enabled?: boolean;
  research_urls?: string[];
  research_query?: string | null;
}

export interface DraftResponse {
  thread_id: string;
  posts: string[];
  awaiting_review: boolean;
  critic_score: number | null;
  critic_violations: string[];
  web_results: WebResult[];
}

export interface DraftVariantsRequest {
  topic: string;
  mode: PostMode;
  style?: string;
  model?: string | null;
  persona_id?: string | null;
  /** Number of parallel variants. Server clamps to 1..5. */
  n: number;
  /** Run the persona critic against each variant. Off by default (slow). */
  score?: boolean;
  research_enabled?: boolean;
  research_urls?: string[];
  research_query?: string | null;
}

export interface DraftVariant {
  index: number;
  posts: string[];
  temperature: number;
  critic_score: number | null;
  critic_violations: string[];
  critic_suggestion: string | null;
  error: string | null;
}

export interface DraftVariantsResponse {
  topic: string;
  mode: PostMode;
  persona_id: string | null;
  variants: DraftVariant[];
  /** Sources used for every variant (research runs once, all variants share). */
  web_results: WebResult[];
}

export interface ApproveRequest {
  action: ApproveAction;
  edited?: string;
}

export interface ApproveResponse {
  thread_id: string;
  posts: string[];
  awaiting_review: boolean;
  finalized: boolean;
  rejected: boolean;
  error: string | null;
  critic_score: number | null;
  critic_violations: string[];
  web_results: WebResult[];
}

export interface ResearchPreviewRequest {
  query?: string | null;
  urls?: string[];
}

export interface ResearchPreviewResponse {
  provider: string;
  query: string;
  urls: string[];
  results: WebResult[];
}

export interface PersonaSummary {
  id: string;
  name: string;
  is_real_person: boolean;
  voice_formality: number;
  voice_brevity: string;
  voice_humor: string;
  updated_at: string;
}

export interface PersonaVoice {
  formality: number;
  brevity: "terse" | "balanced" | "verbose";
  humor: "dry" | "warm" | "sarcastic" | "earnest" | "none";
  sentence_length: "short" | "medium" | "long";
}

export interface PersonaSpec {
  id: string;
  name: string;
  is_real_person: boolean;
  consent_recorded_at: string | null;
  disclosure_text: string;
  voice: PersonaVoice;
  values: string[];
  opinions: string[];
  domains: string[];
  signature_phrases: string[];
  banned_phrases: string[];
  topics_loved: string[];
  topics_avoided: string[];
  decision_style: string;
  confidence_phrasing: string;
  // Richer personality dimensions (added in v0.2.0).
  cadence: string;
  idioms: string[];
  story_seeds: string[];
  pet_peeves: string[];
  enthusiasm_tells: string[];
  conviction_signals: string[];
  apology_pattern: string;
  emotional_range: string;
  // Long-form narrative profile (Markdown) used by the writer prompt.
  personality_md: string;
  created_at: string;
  updated_at: string;
}

export interface InterviewQuestion {
  dimension: string;
  prompt: string;
  kind: QuestionKind;
  is_followup: boolean;
}

export interface InterviewState {
  thread_id: string;
  persona_id: string;
  awaiting_answer: boolean;
  question_index: number;
  total: number;
  question: InterviewQuestion | null;
  saved: boolean;
  error: string | null;
}

export interface PersonaCreateRequest {
  name: string;
  is_real_person: boolean;
  disclosure_text: string;
  consent_ack: boolean;
  quick: boolean;
}

export interface TranscriptEntry {
  dimension: string;
  question: string;
  answer: string;
  is_followup: boolean;
  is_holdout: boolean;
  timestamp: string;
}

export interface QuestionBankEntry {
  dimension: string;
  prompt: string;
  kind: QuestionKind;
  is_holdout: boolean;
}

export interface RefineEntry {
  dimension: string;
  question: string;
  answer: string;
}

export interface EvalScoreEvent {
  topic: string;
  score: number | null;
  violations: string[];
  posts: string[];
  error?: string;
}

export interface EvalDoneEvent {
  count: number;
  scored: number;
  average: number | null;
}
