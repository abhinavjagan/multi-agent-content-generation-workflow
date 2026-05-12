# Persona system

Personas aren't scraped or imported — they're captured. A real person sits down with the agent, answers a structured interview, and that conversation becomes the source of both the example bank and the extracted profile.

## End-to-end

```
interview (Phase A)                           draft (Phase B)
  ask_next_question                             load_persona
    [interrupt for user answer]                   ↳ reads spec.json + personality.md
    progressively appends to transcript.jsonl     ↳ optional embedding retrieval
  judge_followup                                generate_draft
    decides whether to dig deeper                  ↳ system prompt = summarize_for_prompt(personality.md)
  extract (LLM)                                                + MECHANICAL STYLE + guardrails
    → PersonaSpec + personality.md              format_for_x
  embed_and_save                                persona_critic
    → embeddings.npz                              ↳ scores 0-5 vs personality.md
```

## The artifacts

```
~/.x-agent/personas/<id>/
  spec.json          # structured PersonaSpec (machine-readable, used by UI surfaces)
  personality.md     # long-form profile — source of truth the writer prompt reads
  transcript.jsonl   # the raw Q+A (one entry per line, persisted progressively)
  embeddings.npz     # nomic-embed-text vectors over each Q+A
```

Files are `0600`, directories `0700`. Nothing about a persona is ever logged.

## PersonaSpec (structured side)

The schema in `src/x_agent/persona/schema.py` captures the *machine-readable* fingerprint:

| Field | What it captures |
| --- | --- |
| `voice`, `formality`, `brevity` | how the person sounds at a glance |
| `cadence` | sentence rhythm, pauses, fillers |
| `idioms` | favorite words, weird grammar quirks |
| `values`, `strong_opinions` | the worldview |
| `banned_phrases`, `avoided_topics` | hard guardrails |
| `signature_phrases` | actual things they say verbatim |
| `story_seeds` | anecdotes they reach for |
| `pet_peeves`, `enthusiasm_tells`, `conviction_signals` | emotional markers |
| `apology_pattern`, `emotional_range` | how they handle conflict and joy |
| `example_announce`, `example_excited`, `example_morning_pages` | short writing samples |
| `personality_md` | the long-form profile (below) |

The UI uses this for chips and badges. The writer prompt mostly *doesn't* read this directly — it reads…

## personality.md (the source of truth)

`src/x_agent/persona/markdown.py::render_personality_md` produces a long, sectioned profile, ~1-2k words, with verbatim quotes lifted from the transcript so every claim has receipts:

1. **Overview** — one paragraph describing how they come across.
2. **Voice & cadence** — sentence length, punctuation habits, fillers, comma vs. em-dash, etc.
3. **How they sound out loud** — example quotes pulled from the transcript.
4. **Values** with the answers that surfaced each.
5. **Strong opinions** with receipts.
6. **Stories they tell** — the anecdotes they keep returning to.
7. **Signature phrases** (verbatim).
8. **Banned phrases & topics** (hard guardrails).
9. **Decision style** — how they reason about tradeoffs.
10. **Confidence and uncertainty** — what conviction looks like vs. hedging.
11. **Emotional range & enthusiasm tells**.
12. **Apology pattern**.
13. **Three example posts in their voice** — the writing samples they gave.

`summarize_for_prompt(personality_md, max_chars)` is what the writer node actually injects into the system prompt, preserving whole sections where possible so the LLM sees a narrative description of the human, not a bullet cloud.

You can hand-edit `personality.md` whenever you want:

- **UI:** *Personas → pick one → Personality tab → Edit.*
- **CLI:** `x-agent persona edit-md <id>` opens it in `$EDITOR`.
- **API:** `GET /api/personas/{id}/personality` returns the markdown; `PUT` overwrites it.

Whatever you save is what the next draft reads.

## Interview UX

- 17 questions by default, **6 in Quick mode** (great for a first pass — refine later).
- Each question can be **skipped** (records `(skipped)` and moves on).
- Each answer is appended to `transcript.jsonl` *before* the next question, so a crash never loses content.
- The browser mirrors the in-progress state to `localStorage` and offers a **"Save & continue later"** button. Reopening the tab restores the wizard.
- Real-person personas require **explicit consent acknowledgement** and a **disclosure tag** (e.g. `[AI persona of @handle]`). The tag is auto-injected at finalize time; if it would push the first tweet past `MAX_TWEET_CHARS`, the disclosure becomes its own final tweet in the thread.

## Persona consistency critic

After every draft, an LLM judge scores the post 0-5 against `personality.md` and the retrieved transcript chunks. If the score is below `CRITIC_MIN_SCORE` (default 4), the graph loops back to `generate_draft` with the violation list and a one-line suggestion, up to `CRITIC_MAX_ATTEMPTS` (default 2). Then the human still reviews.

## Recovery

If extraction crashes (LLM hiccup, wrong model name, etc.) the transcript is already on disk. Re-run extraction without redoing the interview:

```bash
docker compose exec app x-agent persona resume-extract <persona-id>
```

## HTTP API

| Method | Path | What |
| --- | --- | --- |
| `POST` | `/api/personas` | start an interview; returns `thread_id` and first question |
| `POST` | `/api/personas/{thread_id}/answer` | submit answer; returns next question or `saved: true` |
| `GET` | `/api/personas` | list |
| `GET` | `/api/personas/{id}` | get spec |
| `DELETE` | `/api/personas/{id}` | delete |
| `GET` | `/api/personas/{id}/transcript` | read the raw transcript |
| `GET/PUT` | `/api/personas/{id}/personality` | read/overwrite `personality.md` |
| `POST` | `/api/personas/{id}/eval` | SSE stream scoring the persona on a fixed topic battery |
| `POST` | `/api/personas/{id}/refine` + `/refine/questions` | append more interview answers later |
| `POST` | `/api/personas/{id}/resume-extract` | re-run extraction over the saved transcript |

The disclosure rules are enforced server-side on `human_review.approve` so client-side editing can't strip them.
