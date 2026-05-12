/**
 * Local-only log of finalized drafts.
 *
 * x-agent never publishes anywhere; the moment a user clicks Approve
 * they walk away with a polished artifact. The Dashboard surfaces the
 * last few finalized drafts so they don't lose them across sessions.
 *
 * Storage: ``localStorage`` under a single JSON-encoded key. We cap the
 * log at ``MAX_ENTRIES`` and silently drop oldest entries on write to
 * keep the key bounded (~50 KB worst case).
 */

const STORAGE_KEY = "x-agent:recent-drafts:v1";
const MAX_ENTRIES = 25;

export interface RecentDraft {
  /** Stable id; we use ``crypto.randomUUID()`` when available. */
  id: string;
  /** ISO timestamp of when the draft was finalized. */
  finalized_at: string;
  topic: string;
  mode: "single" | "thread";
  posts: string[];
  persona_id: string | null;
  persona_name: string | null;
  critic_score: number | null;
}

function randomId(): string {
  if (
    typeof globalThis !== "undefined" &&
    typeof globalThis.crypto !== "undefined" &&
    typeof globalThis.crypto.randomUUID === "function"
  ) {
    return globalThis.crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
}

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function listRecentDrafts(): RecentDraft[] {
  const storage = safeStorage();
  if (!storage) return [];
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (e): e is RecentDraft =>
          !!e &&
          typeof e === "object" &&
          typeof (e as RecentDraft).id === "string" &&
          Array.isArray((e as RecentDraft).posts),
      )
      .slice(0, MAX_ENTRIES);
  } catch {
    return [];
  }
}

export function recordRecentDraft(
  entry: Omit<RecentDraft, "id" | "finalized_at"> & {
    id?: string;
    finalized_at?: string;
  },
): RecentDraft {
  const storage = safeStorage();
  const record: RecentDraft = {
    id: entry.id ?? randomId(),
    finalized_at: entry.finalized_at ?? new Date().toISOString(),
    topic: entry.topic,
    mode: entry.mode,
    posts: entry.posts,
    persona_id: entry.persona_id ?? null,
    persona_name: entry.persona_name ?? null,
    critic_score: entry.critic_score ?? null,
  };
  if (!storage) return record;
  const existing = listRecentDrafts();
  const next = [record, ...existing].slice(0, MAX_ENTRIES);
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(next));
    window.dispatchEvent(new Event("x-agent:recent-drafts"));
  } catch {
    // Quota or privacy mode; skip silently.
  }
  return record;
}

export function clearRecentDrafts(): void {
  const storage = safeStorage();
  if (!storage) return;
  try {
    storage.removeItem(STORAGE_KEY);
    window.dispatchEvent(new Event("x-agent:recent-drafts"));
  } catch {
    // ignore
  }
}

/** Generate an X compose deep link for ``text``. */
export function xIntentUrl(text: string): string {
  return `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}`;
}
