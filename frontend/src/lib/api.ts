/**
 * Typed fetch wrapper for the x-agent FastAPI server.
 *
 * Conventions:
 * - All endpoints live under `/api`. In dev, Vite proxies `/api` to the
 *   uvicorn process; in prod we share the origin via StaticFiles.
 * - Non-2xx responses always throw an `ApiError` carrying the parsed
 *   `detail` field when the server provided one.
 * - `streamEval` is a small SSE-on-fetch helper: the Eval endpoint
 *   returns `text/event-stream`, and we parse `event:` / `data:` blocks
 *   incrementally so the UI can render rows live.
 */

import type {
  ApproveRequest,
  ApproveResponse,
  DraftRequest,
  DraftResponse,
  DraftVariantsRequest,
  DraftVariantsResponse,
  EvalDoneEvent,
  EvalScoreEvent,
  Health,
  InterviewState,
  PersonaCreateRequest,
  PersonaSpec,
  PersonaSummary,
  PostMode,
  QuestionBankEntry,
  RefineEntry,
  ResearchPreviewRequest,
  ResearchPreviewResponse,
  TranscriptEntry,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(message: string, status: number, detail: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const BASE = "/api";

async function request<T>(
  path: string,
  init: RequestInit = {},
  parseJson = true,
): Promise<T> {
  const url = `${BASE}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !(init.body instanceof FormData)) {
    headers["Content-Type"] = headers["Content-Type"] ?? "application/json";
  }
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data && typeof data === "object" && "detail" in data) {
        const d = (data as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      // ignore - body wasn't JSON
    }
    throw new ApiError(`${res.status} ${detail}`, res.status, detail);
  }
  if (!parseJson) {
    return undefined as T;
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

function jsonBody(value: unknown): RequestInit {
  return {
    method: "POST",
    body: JSON.stringify(value),
  };
}

// ------------------------------------------------------------------ health

export const getHealth = (): Promise<Health> => request<Health>("/health");

// ------------------------------------------------------------------- draft

export const createDraft = (req: DraftRequest): Promise<DraftResponse> =>
  request<DraftResponse>("/draft", jsonBody(req));

export const createDraftVariants = (
  req: DraftVariantsRequest,
): Promise<DraftVariantsResponse> =>
  request<DraftVariantsResponse>("/draft/variants", jsonBody(req));

export const sendApproval = (
  threadId: string,
  req: ApproveRequest,
): Promise<ApproveResponse> =>
  request<ApproveResponse>(
    `/approve/${encodeURIComponent(threadId)}`,
    jsonBody(req),
  );

// ----------------------------------------------------------------- research

export const previewResearch = (
  req: ResearchPreviewRequest,
): Promise<ResearchPreviewResponse> =>
  request<ResearchPreviewResponse>("/research/preview", jsonBody(req));

// ----------------------------------------------------------------- personas

export const listPersonas = (): Promise<PersonaSummary[]> =>
  request<PersonaSummary[]>("/personas");

export const getPersona = (id: string): Promise<PersonaSpec> =>
  request<PersonaSpec>(`/personas/${encodeURIComponent(id)}`);

export const getPersonaTranscript = (id: string): Promise<TranscriptEntry[]> =>
  request<TranscriptEntry[]>(
    `/personas/${encodeURIComponent(id)}/transcript`,
  );

export const deletePersona = async (id: string): Promise<void> => {
  await request<void>(
    `/personas/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    false,
  );
};

export const startInterview = (
  req: PersonaCreateRequest,
): Promise<InterviewState> =>
  request<InterviewState>("/personas", jsonBody(req));

export const submitAnswer = (
  threadId: string,
  answer: string,
): Promise<InterviewState> =>
  request<InterviewState>(
    `/personas/${encodeURIComponent(threadId)}/answer`,
    jsonBody({ answer }),
  );

export const refineQuestions = (
  personaId: string,
  opts: { dimension?: string; quick?: boolean } = {},
): Promise<QuestionBankEntry[]> => {
  const params = new URLSearchParams();
  if (opts.dimension) params.set("dimension", opts.dimension);
  if (opts.quick) params.set("quick", "true");
  const qs = params.toString();
  return request<QuestionBankEntry[]>(
    `/personas/${encodeURIComponent(personaId)}/refine/questions${
      qs ? `?${qs}` : ""
    }`,
  );
};

export const refinePersona = (
  personaId: string,
  entries: RefineEntry[],
): Promise<PersonaSpec> =>
  request<PersonaSpec>(
    `/personas/${encodeURIComponent(personaId)}/refine`,
    jsonBody({ entries }),
  );

export const resumeExtract = (personaId: string): Promise<PersonaSpec> =>
  request<PersonaSpec>(
    `/personas/${encodeURIComponent(personaId)}/resume-extract`,
    { method: "POST" },
  );

// --------------------------------------------------------------- eval (SSE)

export interface StreamEvalHandlers {
  onScore: (event: EvalScoreEvent) => void;
  onDone: (event: EvalDoneEvent) => void;
  onError?: (err: Error) => void;
  signal?: AbortSignal;
}

export async function streamEval(
  personaId: string,
  body: { prompts?: string[]; mode: PostMode },
  handlers: StreamEvalHandlers,
): Promise<void> {
  const res = await fetch(
    `${BASE}/personas/${encodeURIComponent(personaId)}/eval`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal: handlers.signal,
    },
  );
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data && typeof data === "object" && "detail" in data) {
        detail = String((data as { detail: unknown }).detail);
      }
    } catch {
      // ignore
    }
    throw new ApiError(`${res.status} ${detail}`, res.status, detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (!block.trim()) continue;
        let event = "message";
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) {
            event = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
          }
        }
        const dataRaw = dataLines.join("\n");
        if (!dataRaw) continue;
        try {
          const payload = JSON.parse(dataRaw);
          if (event === "score") {
            handlers.onScore(payload as EvalScoreEvent);
          } else if (event === "done") {
            handlers.onDone(payload as EvalDoneEvent);
          }
        } catch (err) {
          handlers.onError?.(err as Error);
        }
      }
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    handlers.onError?.(err as Error);
    throw err;
  }
}
