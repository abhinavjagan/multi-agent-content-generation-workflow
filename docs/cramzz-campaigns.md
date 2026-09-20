# Cramzz campaign workflow

Cramzz content uses the existing local drafting and human-review boundary. The campaign layer adds a verified fact ledger and an exact-copy state machine; it does not add an X client or credentials.

## Records

- `CampaignBrief` holds the canonical URL, audience, hypothesis, launch window, allowed facts, tone constraints, prohibited claims, and sponsor-disclosure rule.
- `ContentItem` holds the exact post or reply, exact parent URL for replies, media and alt text, evidence references, and status.
- `ApprovalRecord` binds an approver and expiry to a SHA-256 hash of the exact item. Copy, context, link, media, alt text, or evidence changes invalidate it.
- `ApprovalQueue` writes a private local queue and an append-only audit containing only item IDs, content hashes, state transitions, and timestamps.

Actual queue files belong under `~/.x-agent/campaigns/` and should not be committed. The queue serializes mutations with a local file lock and refreshes from disk before every state change. On POSIX systems its directory is `0700` and queue, lock, and audit files are `0600`. The example brief and draft under `campaigns/cramzz/` contain only public, sanitized data.

## State flow

```text
draft → pending_approval → approved → published
             ↘ rejected      ↘ expired
```

Generated content can enter only as `draft`. Approval lasts no more than 24 hours. Any edit is revalidated against its `CampaignBrief`; editing a submitted item returns it to `pending_approval`, while an edited draft remains a draft. Published records are immutable. `mark_published` records that a human sent the approved copy; it performs no network operation.

## Guardrails

- Replies require the exact parent URL.
- Numeric audience, engagement, sponsor, and revenue claims require a fact reference.
- Unknown evidence references and prohibited claims are rejected before review.
- Sponsor-related copy must say `Sponsored`, otherwise explicitly state `No affiliation` when appropriate.
- No model or drafting call may produce `approved` or `published` state.
- Never put passwords, OAuth tokens, browser cookies, raw DMs, payment details, or sponsor-private submissions in a brief, queue, or audit.

The example files can be loaded with Pydantic and queued through `x_agent.campaigns.ApprovalQueue`; see `tests/test_campaign_queue.py` for an end-to-end example. Publication remains copy-to-clipboard or X's own composer, followed by a separate local `mark_published` record.

## Local CLI

```bash
x-agent campaign validate campaigns/cramzz/packet-panic.example.json \
  --item campaigns/cramzz/content-item.example.json
x-agent campaign enqueue \
  --brief campaigns/cramzz/packet-panic.example.json \
  --item campaigns/cramzz/content-item.example.json
x-agent campaign submit packet-panic-launch-post-v1
x-agent campaign list
x-agent campaign approve packet-panic-launch-post-v1 --approver "Abhinav"
# Only after a human actually sends the approved text:
x-agent campaign mark-published packet-panic-launch-post-v1
```

The `approve` command prints the full batch literally (Rich markup in a draft cannot alter the display), including every hash-bound field: identity, channel/type, exact copy, reply context, media, alt text, evidence references, sponsor flag, and full hash. It then requires an interactive confirmation. The queue verifies those displayed hashes again under its file lock before recording approval. It does not open X or send anything.
