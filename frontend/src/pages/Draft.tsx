import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Check,
  CheckCircle2,
  Copy,
  Edit3,
  ExternalLink,
  Globe,
  Layers,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  Thermometer,
  Wand2,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { SourceList } from "@/components/SourceList";
import {
  ApiError,
  createDraft,
  createDraftVariants,
  getHealth,
  listPersonas,
  previewResearch,
  sendApproval,
} from "@/lib/api";
import type {
  ApproveAction,
  ApproveResponse,
  DraftResponse,
  DraftVariant,
  DraftVariantsResponse,
  PostMode,
  WebResult,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface FormState {
  topic: string;
  mode: PostMode;
  style: string;
  model: string;
  personaId: string;
  dryRun: boolean;
  /** 1 = single draft (existing flow); >1 = generate N variants and pick. */
  variantCount: number;
  /** Run the persona critic against each variant. Slow; off by default. */
  scoreVariants: boolean;
  /** Web research opt-in. */
  researchEnabled: boolean;
  /** Newline-separated list of URLs (textarea). When non-empty, takes precedence over the search query. */
  researchUrls: string;
  /** Optional custom query; defaults to the topic when blank. */
  researchQuery: string;
}

interface ReviewState {
  threadId: string;
  posts: string[];
  criticScore: number | null;
  criticViolations: string[];
  webResults: WebResult[];
}

type ResultState =
  | { kind: "idle" }
  | {
      kind: "variants";
      variants: DraftVariant[];
      topic: string;
      mode: PostMode;
      webResults: WebResult[];
    }
  | { kind: "review"; review: ReviewState }
  | { kind: "edit"; review: ReviewState; drafts: string[] }
  | { kind: "done"; response: ApproveResponse; dryRun: boolean }
  | { kind: "rejected" };

const DEFAULT_STYLE = "punchy, technical, plain prose";
const VARIANT_OPTIONS = [1, 3, 5] as const;
const RESEARCH_URL_MAX = 5;

function parseUrlList(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((u) => u.trim())
    .filter(Boolean)
    .slice(0, RESEARCH_URL_MAX);
}

export default function Draft() {
  const [params, setParams] = useSearchParams();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const personas = useQuery({ queryKey: ["personas"], queryFn: listPersonas });

  const [form, setForm] = useState<FormState>(() => ({
    topic: "",
    mode: "thread",
    style: DEFAULT_STYLE,
    model: "",
    personaId: params.get("persona") ?? "",
    dryRun: false,
    variantCount: 1,
    scoreVariants: false,
    researchEnabled: false,
    researchUrls: "",
    researchQuery: "",
  }));
  const [result, setResult] = useState<ResultState>({ kind: "idle" });
  const [previewedSources, setPreviewedSources] = useState<{
    provider: string;
    results: WebResult[];
  } | null>(null);

  // Default the model to the configured one once health resolves.
  useEffect(() => {
    if (!form.model && health.data?.ollama.configured_model) {
      setForm((f) => ({ ...f, model: health.data!.ollama.configured_model }));
    }
  }, [health.data, form.model]);

  // Mirror the persona prefill back into the URL bar so refresh-friendly.
  useEffect(() => {
    const current = params.get("persona") ?? "";
    if (form.personaId !== current) {
      const next = new URLSearchParams(params);
      if (form.personaId) next.set("persona", form.personaId);
      else next.delete("persona");
      setParams(next, { replace: true });
    }
  }, [form.personaId, params, setParams]);

  const researchPayload = () => {
    if (!form.researchEnabled) return {} as const;
    const urls = parseUrlList(form.researchUrls);
    return {
      research_enabled: true,
      research_urls: urls.length ? urls : undefined,
      research_query: form.researchQuery.trim() || undefined,
    } as const;
  };

  const draftMutation = useMutation({
    mutationFn: (input?: { seedPosts?: string[] }) =>
      createDraft({
        topic: form.topic,
        mode: form.mode,
        style: form.style || DEFAULT_STYLE,
        model: form.model || undefined,
        persona_id: form.personaId || undefined,
        dry_run: form.dryRun,
        seed_posts: input?.seedPosts,
        ...researchPayload(),
      }),
    onSuccess: (resp: DraftResponse) => {
      if (!resp.awaiting_review) {
        toast.warning(
          "Backend returned a draft but didn't pause for review. Please check server logs.",
        );
      }
      setResult({
        kind: "review",
        review: {
          threadId: resp.thread_id,
          posts: resp.posts,
          criticScore: resp.critic_score,
          criticViolations: resp.critic_violations,
          webResults: resp.web_results || [],
        },
      });
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
    },
  });

  const variantsMutation = useMutation({
    mutationFn: () =>
      createDraftVariants({
        topic: form.topic,
        mode: form.mode,
        style: form.style || DEFAULT_STYLE,
        model: form.model || undefined,
        persona_id: form.personaId || undefined,
        n: form.variantCount,
        score: form.scoreVariants,
        ...researchPayload(),
      }),
    onSuccess: (resp: DraftVariantsResponse) => {
      // Surface any per-variant generation failures up front so the user
      // doesn't pick an empty card and wonder why it doesn't work.
      const errored = resp.variants.filter((v) => v.error);
      if (errored.length === resp.variants.length) {
        toast.error(
          `All ${resp.variants.length} variants failed. ${
            errored[0]?.error ?? "See server logs."
          }`,
        );
        return;
      }
      if (errored.length > 0) {
        toast.warning(
          `${errored.length} of ${resp.variants.length} variant(s) failed; the rest are below.`,
        );
      }
      setResult({
        kind: "variants",
        variants: resp.variants,
        topic: resp.topic,
        mode: resp.mode,
        webResults: resp.web_results || [],
      });
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
    },
  });

  const previewMutation = useMutation({
    mutationFn: () => {
      const urls = parseUrlList(form.researchUrls);
      const query = form.researchQuery.trim() || form.topic.trim();
      return previewResearch({
        urls: urls.length ? urls : undefined,
        query: query || undefined,
      });
    },
    onSuccess: (resp) => {
      setPreviewedSources({ provider: resp.provider, results: resp.results });
      if (resp.results.length === 0) {
        toast.warning(
          "No sources found. Try a more specific query or paste URLs.",
        );
      } else {
        toast.success(
          `Found ${resp.results.length} source${
            resp.results.length === 1 ? "" : "s"
          } via ${resp.provider}.`,
        );
      }
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
    },
  });

  const approveMutation = useMutation({
    mutationFn: (input: { action: ApproveAction; edited?: string }) => {
      if (result.kind !== "review" && result.kind !== "edit") {
        throw new Error("no thread to approve");
      }
      const threadId = result.review.threadId;
      return sendApproval(threadId, input);
    },
    onSuccess: (resp: ApproveResponse) => {
      if (resp.error) {
        toast.error(resp.error);
      }
      if (resp.rejected) {
        setResult({ kind: "rejected" });
        return;
      }
      if (resp.awaiting_review) {
        setResult({
          kind: "review",
          review: {
            threadId: resp.thread_id,
            posts: resp.posts,
            criticScore: resp.critic_score,
            criticViolations: resp.critic_violations,
            // The backend re-emits ``web_results`` on every approve turn,
            // but if it ever stops we keep the previously-shown sources so
            // the reviewer doesn't lose them mid-flow.
            webResults:
              resp.web_results && resp.web_results.length > 0
                ? resp.web_results
                : (result.kind === "review" || result.kind === "edit"
                    ? result.review.webResults
                    : []) || [],
          },
        });
        return;
      }
      // Final state: posted (or dry-run posted).
      const dryRun =
        form.dryRun ||
        !health.data?.x.has_credentials ||
        !!resp.error;
      setResult({ kind: "done", response: resp, dryRun });
      if (!resp.error) {
        toast.success(
          dryRun
            ? `Dry-run complete: ${resp.tweet_ids.length} tweet(s) simulated`
            : `Posted ${resp.tweet_ids.length} tweet(s)`,
        );
      }
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
    },
  });

  const submitDisabled =
    !form.topic.trim() ||
    draftMutation.isPending ||
    approveMutation.isPending ||
    variantsMutation.isPending ||
    !health.data?.ollama.has_configured_model;

  const isBusy =
    draftMutation.isPending ||
    approveMutation.isPending ||
    variantsMutation.isPending;

  const researchProvider =
    health.data?.config?.research?.active_provider ?? "duckduckgo";
  const researchUrlList = useMemo(
    () => parseUrlList(form.researchUrls),
    [form.researchUrls],
  );

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr),360px]">
      <div className="space-y-6">
        {result.kind === "variants" ? (
          <VariantsCard
            variants={result.variants}
            topic={result.topic}
            mode={result.mode}
            webResults={result.webResults}
            picking={draftMutation.isPending}
            maxChars={health.data?.x.max_tweet_chars ?? 275}
            onPick={(variant) => {
              if (!variant.posts.length) {
                toast.error("That variant has no posts to seed from.");
                return;
              }
              draftMutation.mutate({ seedPosts: variant.posts });
            }}
            onRegenerate={() => variantsMutation.mutate()}
            onCancel={() => setResult({ kind: "idle" })}
          />
        ) : null}

        {result.kind === "review" || result.kind === "edit" ? (
          <ReviewCard
            mode={form.mode}
            review={result.review}
            editing={result.kind === "edit"}
            drafts={result.kind === "edit" ? result.drafts : result.review.posts}
            busy={approveMutation.isPending}
            maxChars={health.data?.x.max_tweet_chars ?? 275}
            onStartEdit={() =>
              setResult({
                kind: "edit",
                review: result.review,
                drafts: [...result.review.posts],
              })
            }
            onCancelEdit={() =>
              setResult({ kind: "review", review: result.review })
            }
            onChangeDraft={(idx, value) => {
              if (result.kind !== "edit") return;
              const drafts = [...result.drafts];
              drafts[idx] = value;
              setResult({ ...result, drafts });
            }}
            onApprove={() => approveMutation.mutate({ action: "approve" })}
            onRegenerate={() => approveMutation.mutate({ action: "regenerate" })}
            onReject={() => approveMutation.mutate({ action: "reject" })}
            onSubmitEdit={() => {
              if (result.kind !== "edit") return;
              const edited = result.drafts
                .map((t) => t.trim())
                .filter(Boolean)
                .join("\n\n");
              if (!edited) {
                toast.error("Edit cannot be empty");
                return;
              }
              approveMutation.mutate({ action: "edit", edited });
            }}
          />
        ) : null}

        {result.kind === "done" ? (
          <PostedCard response={result.response} dryRun={result.dryRun} />
        ) : null}

        {result.kind === "rejected" ? (
          <Alert variant="destructive">
            <XCircle className="h-4 w-4" />
            <div className="space-y-1">
              <AlertTitle>Rejected.</AlertTitle>
              <AlertDescription>
                Nothing was posted. Use the form to start over.
              </AlertDescription>
            </div>
          </Alert>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Wand2 className="h-5 w-5 text-primary" />
              Compose a draft
            </CardTitle>
            <CardDescription>
              The agent will generate, run a critic, then pause here for your
              review.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="topic">Topic</Label>
              <Textarea
                id="topic"
                placeholder="e.g. Why local LLMs matter in 2026"
                value={form.topic}
                onChange={(e) => setForm({ ...form, topic: e.target.value })}
                maxLength={280}
                rows={3}
                autoSize
                disabled={isBusy}
              />
              <p className="text-right text-xs text-muted-foreground">
                {form.topic.length}/280
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Mode</Label>
                <Select
                  value={form.mode}
                  onChange={(e) =>
                    setForm({ ...form, mode: e.target.value as PostMode })
                  }
                  disabled={isBusy}
                >
                  <option value="thread">thread</option>
                  <option value="single">single</option>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Persona</Label>
                <Select
                  value={form.personaId}
                  onChange={(e) =>
                    setForm({ ...form, personaId: e.target.value })
                  }
                  disabled={isBusy}
                >
                  <option value="">(none)</option>
                  {personas.data?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} — {p.id}
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Variants</Label>
                <Select
                  value={String(form.variantCount)}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      variantCount: parseInt(e.target.value, 10),
                    })
                  }
                  disabled={isBusy}
                >
                  {VARIANT_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n === 1 ? "1 (single draft)" : `${n} variants to pick from`}
                    </option>
                  ))}
                </Select>
                <p className="text-xs text-muted-foreground">
                  {form.variantCount > 1
                    ? `Generates ${form.variantCount} drafts in parallel at different temperatures, then you pick one.`
                    : "Goes straight to the review screen."}
                </p>
              </div>
              <div className="flex items-end justify-between gap-3 rounded-md border border-border p-3">
                <div className="space-y-1">
                  <Label htmlFor="score-variants" className="cursor-pointer">
                    Score each variant
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    {form.variantCount === 1
                      ? "N/A for a single draft."
                      : form.personaId
                        ? "Runs the persona critic per variant. Doubles latency."
                        : "Needs a persona; ignored otherwise."}
                  </p>
                </div>
                <Switch
                  id="score-variants"
                  checked={form.scoreVariants}
                  onCheckedChange={(v) =>
                    setForm({ ...form, scoreVariants: v })
                  }
                  disabled={
                    isBusy || form.variantCount === 1 || !form.personaId
                  }
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="style">Style hint</Label>
              <Input
                id="style"
                value={form.style}
                placeholder={DEFAULT_STYLE}
                onChange={(e) => setForm({ ...form, style: e.target.value })}
                maxLength={200}
                disabled={isBusy}
              />
            </div>

            <div className="rounded-md border border-border p-4 space-y-4">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <Label
                    htmlFor="research-toggle"
                    className="cursor-pointer flex items-center gap-2"
                  >
                    <Globe className="h-4 w-4 text-primary" />
                    Ground in web sources
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    {form.researchEnabled
                      ? `On — your ${
                          researchUrlList.length > 0 ? "URLs" : "topic / query"
                        } will be sent to ${researchProvider}.`
                      : "Off — the agent only sees your persona and examples."}
                  </p>
                </div>
                <Switch
                  id="research-toggle"
                  checked={form.researchEnabled}
                  onCheckedChange={(v) => {
                    setForm({ ...form, researchEnabled: v });
                    if (!v) setPreviewedSources(null);
                  }}
                  disabled={isBusy}
                />
              </div>

              {form.researchEnabled ? (
                <div className="space-y-3">
                  <div className="space-y-2">
                    <Label htmlFor="research-urls">
                      URLs to summarize (one per line, max {RESEARCH_URL_MAX})
                    </Label>
                    <Textarea
                      id="research-urls"
                      placeholder={"https://example.com/post\nhttps://blog.example/article"}
                      value={form.researchUrls}
                      onChange={(e) =>
                        setForm({ ...form, researchUrls: e.target.value })
                      }
                      rows={3}
                      autoSize
                      disabled={isBusy}
                    />
                    <p className="text-xs text-muted-foreground">
                      {researchUrlList.length === 0
                        ? "Empty → topic-driven web search."
                        : `${researchUrlList.length} URL${
                            researchUrlList.length === 1 ? "" : "s"
                          } will be fetched (search skipped).`}
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="research-query">
                      Search query override
                    </Label>
                    <Input
                      id="research-query"
                      value={form.researchQuery}
                      placeholder="defaults to topic"
                      onChange={(e) =>
                        setForm({ ...form, researchQuery: e.target.value })
                      }
                      maxLength={500}
                      disabled={
                        isBusy || researchUrlList.length > 0
                      }
                    />
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[11px] text-muted-foreground">
                      Provider: <span className="font-mono">{researchProvider}</span>
                    </p>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => previewMutation.mutate()}
                      loading={previewMutation.isPending}
                      disabled={
                        isBusy ||
                        previewMutation.isPending ||
                        (!form.topic.trim() &&
                          !form.researchQuery.trim() &&
                          researchUrlList.length === 0)
                      }
                    >
                      <Search className="h-4 w-4" />
                      Preview sources
                    </Button>
                  </div>
                  {previewedSources ? (
                    <SourceList
                      results={previewedSources.results}
                      provider={previewedSources.provider}
                      label={`Preview · ${previewedSources.results.length} source${
                        previewedSources.results.length === 1 ? "" : "s"
                      }`}
                    />
                  ) : null}
                </div>
              ) : null}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Model override</Label>
                <Select
                  value={form.model}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                  disabled={isBusy}
                >
                  {(health.data?.ollama.available_models ?? []).map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                  {health.data?.ollama.available_models?.length === 0 ? (
                    <option value="">(no models pulled)</option>
                  ) : null}
                </Select>
              </div>
              <div className="flex items-end justify-between gap-3 rounded-md border border-border p-3">
                <div className="space-y-1">
                  <Label htmlFor="dry-run" className="cursor-pointer">
                    Dry-run
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    {health.data?.x.has_credentials
                      ? "When on, never calls X."
                      : "Forced on (no X creds)."}
                  </p>
                </div>
                <Switch
                  id="dry-run"
                  checked={form.dryRun || !health.data?.x.has_credentials}
                  onCheckedChange={(v) =>
                    setForm({ ...form, dryRun: v })
                  }
                  disabled={!health.data?.x.has_credentials || isBusy}
                />
              </div>
            </div>
          </CardContent>
          <CardFooter className="justify-end">
            <Button
              size="lg"
              onClick={() => {
                setResult({ kind: "idle" });
                if (form.variantCount > 1) {
                  variantsMutation.mutate();
                } else {
                  draftMutation.mutate(undefined);
                }
              }}
              loading={draftMutation.isPending || variantsMutation.isPending}
              disabled={submitDisabled}
            >
              {form.variantCount > 1 ? (
                <>
                  <Layers className="h-4 w-4" />
                  Generate {form.variantCount} variants
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  Generate draft
                </>
              )}
            </Button>
          </CardFooter>
        </Card>
      </div>

      <aside className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">How review works</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <p>
              When the draft is ready you'll see each tweet with its character
              count and a persona consistency score (when a persona is
              selected).
            </p>
            <ul className="list-disc space-y-1 pl-5">
              <li>
                <span className="text-foreground">Approve</span> publishes (or
                simulates in dry-run).
              </li>
              <li>
                <span className="text-foreground">Edit</span> lets you tweak
                each tweet inline before posting.
              </li>
              <li>
                <span className="text-foreground">Regenerate</span> asks the
                model again, possibly looping the critic.
              </li>
              <li>
                <span className="text-foreground">Reject</span> ends the run
                without posting.
              </li>
            </ul>
          </CardContent>
        </Card>
        {form.personaId ? <PersonaHint personaId={form.personaId} /> : null}
      </aside>
    </div>
  );
}

function PersonaHint({ personaId }: { personaId: string }) {
  const personas = useQuery({ queryKey: ["personas"], queryFn: listPersonas });
  const persona = personas.data?.find((p) => p.id === personaId);
  if (!persona) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          Drafting as {persona.name}
        </CardTitle>
        <CardDescription className="font-mono text-xs">
          {persona.id}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-1.5 pt-0">
        <Badge variant="outline">formality {persona.voice_formality}/5</Badge>
        <Badge variant="outline">{persona.voice_brevity}</Badge>
        <Badge variant="outline">{persona.voice_humor}</Badge>
        {persona.is_real_person ? (
          <Badge variant="warning">real person</Badge>
        ) : (
          <Badge variant="muted">fictional</Badge>
        )}
      </CardContent>
    </Card>
  );
}

interface ReviewCardProps {
  mode: PostMode;
  review: ReviewState;
  editing: boolean;
  drafts: string[];
  busy: boolean;
  maxChars: number;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onChangeDraft: (idx: number, value: string) => void;
  onApprove: () => void;
  onRegenerate: () => void;
  onReject: () => void;
  onSubmitEdit: () => void;
}

function ReviewCard({
  mode,
  review,
  editing,
  drafts,
  busy,
  maxChars,
  onStartEdit,
  onCancelEdit,
  onChangeDraft,
  onApprove,
  onRegenerate,
  onReject,
  onSubmitEdit,
}: ReviewCardProps) {
  const score = review.criticScore;
  const scoreVariant: "success" | "warning" | "destructive" | "muted" =
    score == null
      ? "muted"
      : score >= 4
        ? "success"
        : score >= 2
          ? "warning"
          : "destructive";
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              Draft ready
            </CardTitle>
            <CardDescription>
              {drafts.length} {drafts.length === 1 ? "tweet" : "tweets"} ·{" "}
              <span className="font-mono">{mode}</span>
            </CardDescription>
          </div>
          {score != null ? (
            <Badge variant={scoreVariant}>
              critic {score}/5
            </Badge>
          ) : null}
        </div>
        {review.criticViolations.length ? (
          <ul className="mt-2 space-y-1 text-xs text-warning">
            {review.criticViolations.slice(0, 4).map((v, i) => (
              <li key={i}>· {v}</li>
            ))}
          </ul>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {drafts.map((post, idx) => (
          <TweetCard
            key={idx}
            index={idx}
            total={drafts.length}
            text={post}
            editable={editing}
            maxChars={maxChars}
            onChange={(v) => onChangeDraft(idx, v)}
          />
        ))}
        {review.webResults.length > 0 ? (
          <SourceList results={review.webResults} compact />
        ) : null}
      </CardContent>
      <CardFooter className="flex-wrap gap-2">
        {editing ? (
          <>
            <Button
              variant="outline"
              onClick={onCancelEdit}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button onClick={onSubmitEdit} loading={busy}>
              <Check className="h-4 w-4" />
              Save edit + approve
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="destructive"
              onClick={onReject}
              loading={busy}
              disabled={busy}
            >
              <XCircle className="h-4 w-4" />
              Reject
            </Button>
            <Button
              variant="outline"
              onClick={onRegenerate}
              loading={busy}
              disabled={busy}
            >
              <RefreshCw className="h-4 w-4" />
              Regenerate
            </Button>
            <Button
              variant="secondary"
              onClick={onStartEdit}
              disabled={busy}
            >
              <Edit3 className="h-4 w-4" />
              Edit
            </Button>
            <Button onClick={onApprove} loading={busy} disabled={busy}>
              <Send className="h-4 w-4" />
              Approve & post
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  );
}

interface TweetCardProps {
  index: number;
  total: number;
  text: string;
  editable: boolean;
  maxChars: number;
  onChange: (value: string) => void;
}

function TweetCard({
  index,
  total,
  text,
  editable,
  maxChars,
  onChange,
}: TweetCardProps) {
  const [copied, setCopied] = useState(false);
  const overLimit = text.length > maxChars;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Could not copy to clipboard");
    }
  };

  return (
    <div
      className={cn(
        "rounded-lg border bg-background/40 p-4",
        editable ? "border-primary/40" : "border-border",
        overLimit && "ring-1 ring-destructive/40",
      )}
    >
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-medium text-muted-foreground">
          Tweet {index + 1}/{total}
        </span>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "font-mono",
              overLimit ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {text.length}/{maxChars}
          </span>
          {!editable ? (
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
              aria-label="Copy tweet"
              title="Copy"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-success" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </button>
          ) : null}
        </div>
      </div>
      {editable ? (
        <Textarea
          autoSize
          value={text}
          onChange={(e) => onChange(e.target.value)}
          className="bg-background"
        />
      ) : (
        <p className="whitespace-pre-wrap font-mono text-sm leading-relaxed">
          {text}
        </p>
      )}
    </div>
  );
}

function PostedCard({
  response,
  dryRun,
}: {
  response: ApproveResponse;
  dryRun: boolean;
}) {
  const tweets = useMemo(() => response.posts, [response.posts]);
  return (
    <Card className="border-success/40 bg-success/5">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-success" />
          {dryRun ? "Dry-run complete" : "Posted to X"}
        </CardTitle>
        <CardDescription>
          {response.tweet_ids.length} tweet
          {response.tweet_ids.length === 1 ? "" : "s"}
          {response.error ? ` · error: ${response.error}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {tweets.map((post, idx) => (
          <div
            key={idx}
            className="rounded-md border border-border bg-background/60 p-3 text-sm"
          >
            <p className="mb-1 text-xs text-muted-foreground">
              Tweet {idx + 1}/{tweets.length}
              {response.tweet_ids[idx]
                ? ` · id ${response.tweet_ids[idx]}`
                : ""}
            </p>
            <p className="whitespace-pre-wrap font-mono leading-relaxed">
              {post}
            </p>
          </div>
        ))}
      </CardContent>
      {response.tweet_url ? (
        <CardFooter>
          <Button asChild variant="outline">
            <a
              href={response.tweet_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink className="h-4 w-4" />
              View on X
            </a>
          </Button>
        </CardFooter>
      ) : null}
    </Card>
  );
}

// ----------------------------------------------------------------- variants

interface VariantsCardProps {
  variants: DraftVariant[];
  topic: string;
  mode: PostMode;
  webResults: WebResult[];
  picking: boolean;
  maxChars: number;
  onPick: (variant: DraftVariant) => void;
  onRegenerate: () => void;
  onCancel: () => void;
}

function VariantsCard({
  variants,
  topic,
  mode,
  webResults,
  picking,
  maxChars,
  onPick,
  onRegenerate,
  onCancel,
}: VariantsCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Layers className="h-5 w-5 text-primary" />
              Pick a variant
            </CardTitle>
            <CardDescription>
              {variants.length} drafts of{" "}
              <span className="font-mono">"{truncate(topic, 60)}"</span> ·{" "}
              <span className="font-mono">{mode}</span>
              {webResults.length > 0
                ? ` · grounded in ${webResults.length} source${
                    webResults.length === 1 ? "" : "s"
                  }`
                : ""}
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            disabled={picking}
          >
            Cancel
          </Button>
        </div>
      </CardHeader>
      <CardContent
        className={cn(
          "grid gap-3",
          variants.length >= 3 ? "lg:grid-cols-2" : "",
        )}
      >
        {variants.map((variant) => (
          <VariantCard
            key={variant.index}
            variant={variant}
            maxChars={maxChars}
            picking={picking}
            onPick={() => onPick(variant)}
          />
        ))}
      </CardContent>
      {webResults.length > 0 ? (
        <CardContent className="pt-0">
          <SourceList
            results={webResults}
            label={`Sources used (${webResults.length})`}
            compact
          />
        </CardContent>
      ) : null}
      <CardFooter className="justify-end">
        <Button
          variant="outline"
          onClick={onRegenerate}
          loading={picking}
          disabled={picking}
        >
          <RefreshCw className="h-4 w-4" />
          Regenerate all
        </Button>
      </CardFooter>
    </Card>
  );
}

interface VariantCardProps {
  variant: DraftVariant;
  maxChars: number;
  picking: boolean;
  onPick: () => void;
}

function VariantCard({ variant, maxChars, picking, onPick }: VariantCardProps) {
  const [copied, setCopied] = useState(false);
  const score = variant.critic_score;
  const scoreVariant: "success" | "warning" | "destructive" | "muted" =
    score == null
      ? "muted"
      : score >= 4
        ? "success"
        : score >= 2
          ? "warning"
          : "destructive";
  const fullText = useMemo(
    () => variant.posts.join("\n\n"),
    [variant.posts],
  );
  const totalChars = fullText.length;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(fullText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Could not copy to clipboard");
    }
  };

  if (variant.error) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
        <div className="mb-2 flex items-center justify-between">
          <span className="font-medium">Variant {variant.index + 1}</span>
          <Badge variant="destructive">failed</Badge>
        </div>
        <p className="text-xs text-muted-foreground">{variant.error}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col rounded-lg border border-border bg-background/40 p-4">
      <div className="mb-3 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <span className="font-medium">Variant {variant.index + 1}</span>
          <Badge variant="muted" className="font-mono text-[10px]">
            <Thermometer className="mr-1 h-3 w-3" />
            {variant.temperature.toFixed(2)}
          </Badge>
          {score != null ? (
            <Badge variant={scoreVariant}>critic {score}/5</Badge>
          ) : null}
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Copy variant"
          title="Copy"
          disabled={picking}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-success" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
      <div className="flex-1 space-y-2">
        {variant.posts.map((post, idx) => (
          <div
            key={idx}
            className={cn(
              "rounded-md border border-border/60 bg-background/60 p-2.5",
              post.length > maxChars && "ring-1 ring-destructive/40",
            )}
          >
            {variant.posts.length > 1 ? (
              <p className="mb-1 text-[10px] text-muted-foreground">
                Tweet {idx + 1}/{variant.posts.length} ·{" "}
                <span className="font-mono">
                  {post.length}/{maxChars}
                </span>
              </p>
            ) : null}
            <p className="whitespace-pre-wrap font-mono text-xs leading-relaxed">
              {post}
            </p>
          </div>
        ))}
      </div>
      {variant.critic_violations.length ? (
        <ul className="mt-2 space-y-0.5 text-[11px] text-warning">
          {variant.critic_violations.slice(0, 3).map((v, i) => (
            <li key={i}>· {v}</li>
          ))}
        </ul>
      ) : null}
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">
          {variant.posts.length} {variant.posts.length === 1 ? "tweet" : "tweets"}{" "}
          · {totalChars} chars
        </span>
        <Button
          size="sm"
          onClick={onPick}
          loading={picking}
          disabled={picking || variant.posts.length === 0}
        >
          <Check className="h-3.5 w-3.5" />
          Pick this one
        </Button>
      </div>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
