import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BookOpen,
  ChartLine,
  Check,
  Edit3,
  Hammer,
  MessageSquare,
  Quote,
  Save,
  Trash2,
  Wand2,
  X,
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
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { EmptyState } from "@/components/ui/empty";
import {
  ApiError,
  deletePersona,
  getPersona,
  getPersonaPersonality,
  getPersonaTranscript,
  putPersonaPersonality,
  resumeExtract,
} from "@/lib/api";
import { formatRelative } from "@/lib/utils";
import type { PersonaSpec } from "@/lib/types";

export default function PersonaDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const persona = useQuery({
    queryKey: ["persona", id],
    queryFn: () => getPersona(id),
    enabled: !!id,
  });

  const deleteMutation = useMutation({
    mutationFn: () => deletePersona(id),
    onSuccess: () => {
      toast.success(`Deleted ${id}`);
      qc.invalidateQueries({ queryKey: ["personas"] });
      qc.invalidateQueries({ queryKey: ["health"] });
      navigate("/personas");
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message, {
        description:
          "Refresh the list. If the entry sticks, remove ~/.x-agent/personas/<id> by hand.",
      });
    },
  });

  const resumeMutation = useMutation({
    mutationFn: () => resumeExtract(id),
    onSuccess: (spec) => {
      toast.success("Re-extracted persona spec");
      qc.setQueryData(["persona", id], spec);
      qc.invalidateQueries({ queryKey: ["personas"] });
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message, {
        description:
          "Most likely the Ollama generation model isn't pulled. Verify with `ollama list` and check Settings.",
      });
    },
  });

  if (persona.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-1/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (persona.error || !persona.data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Failed to load persona</AlertTitle>
        <AlertDescription>
          {persona.error instanceof ApiError
            ? persona.error.detail
            : "Unknown error"}
        </AlertDescription>
      </Alert>
    );
  }

  const spec = persona.data;

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center justify-between gap-3">
        <div>
          <Button asChild variant="ghost" size="sm" className="-ml-2">
            <Link to="/personas">
              <ArrowLeft className="h-4 w-4" />
              All personas
            </Link>
          </Button>
          <div className="mt-2 flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">
              {spec.name}
            </h1>
            {spec.is_real_person ? (
              <Badge variant="default">real person</Badge>
            ) : (
              <Badge variant="secondary">fictional</Badge>
            )}
          </div>
          <p className="font-mono text-xs text-muted-foreground">{spec.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild>
            <Link to={`/draft?persona=${spec.id}`}>
              <Wand2 className="h-4 w-4" />
              Draft as
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to={`/personas/${spec.id}/refine`}>
              <Edit3 className="h-4 w-4" />
              Refine
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link to={`/personas/${spec.id}/eval`}>
              <ChartLine className="h-4 w-4" />
              Evaluate
            </Link>
          </Button>
        </div>
      </div>

      <Tabs defaultValue="personality">
        <TabsList>
          <TabsTrigger value="personality">Personality</TabsTrigger>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="transcript">Transcript</TabsTrigger>
          <TabsTrigger value="actions">Danger zone</TabsTrigger>
        </TabsList>

        <TabsContent value="personality">
          <PersonalityTab id={spec.id} />
        </TabsContent>

        <TabsContent value="overview">
          <OverviewTab spec={spec} />
        </TabsContent>

        <TabsContent value="transcript">
          <TranscriptTab id={spec.id} />
        </TabsContent>

        <TabsContent value="actions">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Recovery</CardTitle>
              <CardDescription>
                If extraction failed (e.g. wrong Ollama model), re-run it
                against the saved transcript without redoing the interview.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button
                variant="outline"
                loading={resumeMutation.isPending}
                onClick={() => resumeMutation.mutate()}
              >
                <Hammer className="h-4 w-4" />
                Resume extraction
              </Button>
            </CardContent>
          </Card>

          <div className="mt-4">
            <Card className="border-destructive/30">
              <CardHeader>
                <CardTitle className="text-base text-destructive">
                  Delete persona
                </CardTitle>
                <CardDescription>
                  Removes the spec, transcript, and embeddings. Cannot be
                  undone.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Button
                  variant="destructive"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 className="h-4 w-4" />
                  Delete
                </Button>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <Dialog
        open={confirmDelete}
        onOpenChange={(open) => !open && setConfirmDelete(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this persona?</DialogTitle>
            <DialogDescription>
              This permanently removes{" "}
              <span className="font-medium text-foreground">{spec.name}</span>{" "}
              including the interview transcript and embeddings.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              loading={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate()}
            >
              <Trash2 className="h-4 w-4" />
              Delete persona
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function OverviewTab({ spec }: { spec: PersonaSpec }) {
  const groups: { label: string; items: string[]; tone?: "default" | "destructive" }[] = useMemo(
    () => [
      { label: "Values", items: spec.values },
      { label: "Opinions", items: spec.opinions },
      { label: "Conviction signals", items: spec.conviction_signals ?? [] },
      { label: "Domains", items: spec.domains },
      { label: "Topics they love", items: spec.topics_loved },
      { label: "Topics they avoid", items: spec.topics_avoided },
      { label: "Signature phrases", items: spec.signature_phrases },
      { label: "Idioms & quirks", items: spec.idioms ?? [] },
      { label: "Enthusiasm tells", items: spec.enthusiasm_tells ?? [] },
      { label: "Pet peeves", items: spec.pet_peeves ?? [] },
      { label: "Story seeds", items: spec.story_seeds ?? [] },
      { label: "Banned phrases", items: spec.banned_phrases, tone: "destructive" },
    ],
    [spec],
  );

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-1">
        <CardHeader>
          <CardTitle className="text-base">Voice</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <DataRow label="Formality" value={`${spec.voice.formality}/5`} />
          <DataRow label="Brevity" value={spec.voice.brevity} />
          <DataRow label="Humor" value={spec.voice.humor} />
          <DataRow label="Sentence length" value={spec.voice.sentence_length} />
          <Separator />
          <DataRow
            label="Created"
            value={formatRelative(spec.created_at)}
            mono
          />
          <DataRow
            label="Updated"
            value={formatRelative(spec.updated_at)}
            mono
          />
          {spec.disclosure_text ? (
            <div className="pt-2">
              <p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
                Disclosure
              </p>
              <p className="rounded-md bg-secondary p-2 font-mono text-xs">
                {spec.disclosure_text}
              </p>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">Distilled persona</CardTitle>
          <CardDescription>
            These chips drive the writer prompt and the persona-consistency
            critic.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {groups.map((g) => (
            <div key={g.label}>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                {g.label}
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {g.items.length ? (
                  g.items.map((item, i) => (
                    <Badge
                      key={i}
                      variant={g.tone === "destructive" ? "destructive" : "outline"}
                    >
                      {item}
                    </Badge>
                  ))
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </div>
            </div>
          ))}
          {spec.decision_style ? (
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                Decision style
              </p>
              <p className="mt-1 text-sm leading-relaxed">{spec.decision_style}</p>
            </div>
          ) : null}
          {spec.confidence_phrasing ? (
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                Confidence phrasing
              </p>
              <p className="mt-1 text-sm leading-relaxed">
                {spec.confidence_phrasing}
              </p>
            </div>
          ) : null}
          {spec.cadence ? (
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                Cadence
              </p>
              <p className="mt-1 text-sm leading-relaxed">{spec.cadence}</p>
            </div>
          ) : null}
          {spec.emotional_range ? (
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                Emotional range
              </p>
              <p className="mt-1 text-sm leading-relaxed">{spec.emotional_range}</p>
            </div>
          ) : null}
          {spec.apology_pattern ? (
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                Apology pattern
              </p>
              <p className="mt-1 text-sm leading-relaxed">{spec.apology_pattern}</p>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

function PersonalityTab({ id }: { id: string }) {
  const qc = useQueryClient();
  const profile = useQuery({
    queryKey: ["persona", id, "personality"],
    queryFn: () => getPersonaPersonality(id),
    enabled: !!id,
  });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    if (!editing && profile.data) {
      setDraft(profile.data.markdown);
    }
  }, [profile.data, editing]);

  const saveMutation = useMutation({
    mutationFn: () => putPersonaPersonality(id, draft),
    onSuccess: (resp) => {
      qc.setQueryData(["persona", id, "personality"], resp);
      qc.invalidateQueries({ queryKey: ["persona", id] });
      qc.invalidateQueries({ queryKey: ["personas"] });
      setEditing(false);
      toast.success("Saved personality.md.", {
        description: "The writer prompt picks up your edits on the next draft.",
      });
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.detail : (err as Error).message,
        {
          description:
            "Check the markdown length (≤40k chars) and that the server is reachable.",
        },
      );
    },
  });

  if (profile.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (profile.error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Couldn't load personality.md</AlertTitle>
        <AlertDescription>
          {profile.error instanceof ApiError
            ? profile.error.detail
            : (profile.error as Error).message}
        </AlertDescription>
      </Alert>
    );
  }

  const md = profile.data?.markdown ?? "";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <BookOpen className="h-5 w-5 text-primary" />
              personality.md
            </CardTitle>
            <CardDescription>
              The long-form profile the writer prompt reads. Hand-edit freely;
              changes apply to the next draft.
            </CardDescription>
          </div>
          {!editing ? (
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Edit3 className="h-4 w-4" />
              Edit
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        {md.trim().length === 0 && !editing ? (
          <EmptyState
            icon={<BookOpen className="h-5 w-5" />}
            title="No personality.md yet"
            description="Re-run the interview or click Refine to produce one."
          />
        ) : editing ? (
          <Textarea
            autoSize
            rows={20}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="font-mono text-sm leading-relaxed"
            maxLength={40_000}
          />
        ) : (
          <article className="prose-personality">
            <RenderMarkdown markdown={md} />
          </article>
        )}
      </CardContent>
      {editing ? (
        <CardFooter className="flex-wrap gap-2">
          <Button
            variant="ghost"
            onClick={() => {
              setEditing(false);
              setDraft(md);
            }}
            disabled={saveMutation.isPending}
          >
            <X className="h-4 w-4" />
            Cancel
          </Button>
          <Button
            onClick={() => saveMutation.mutate()}
            loading={saveMutation.isPending}
            disabled={draft.trim() === md.trim()}
          >
            <Save className="h-4 w-4" />
            Save changes
          </Button>
          <p className="ml-auto text-[11px] text-muted-foreground">
            {draft.length}/40,000
          </p>
        </CardFooter>
      ) : md.trim().length > 0 ? (
        <CardFooter>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(md);
                toast.success("Copied personality.md to clipboard.");
              } catch {
                toast.error("Clipboard not available.");
              }
            }}
          >
            <Check className="h-4 w-4" />
            Copy markdown
          </Button>
        </CardFooter>
      ) : null}
    </Card>
  );
}

/**
 * Tiny, dependency-free markdown renderer.
 *
 * We render exactly the constructs ``render_personality_md`` produces:
 * ``#``/``##``/``###`` headings, ``-`` bullets, ``>`` blockquotes,
 * bold via ``**text**``, italic via ``_text_``, and inline code via
 * backticks. Anything else falls through as plain text. This keeps us
 * off the npm bandwagon while giving the user a readable rendering.
 * All output is text-content only, so it's XSS-safe by construction.
 */
function RenderMarkdown({ markdown }: { markdown: string }) {
  const lines = markdown.split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^#\s/.test(line)) {
      out.push(
        <h1 key={key++} className="mt-4 text-2xl font-semibold tracking-tight">
          {renderInline(line.slice(2))}
        </h1>,
      );
      i++;
      continue;
    }
    if (/^##\s/.test(line)) {
      out.push(
        <h2 key={key++} className="mt-6 border-b border-border/40 pb-1 text-lg font-semibold tracking-tight">
          {renderInline(line.slice(3))}
        </h2>,
      );
      i++;
      continue;
    }
    if (/^###\s/.test(line)) {
      out.push(
        <h3 key={key++} className="mt-4 text-base font-semibold">
          {renderInline(line.slice(4))}
        </h3>,
      );
      i++;
      continue;
    }
    if (/^-\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^-\s/.test(lines[i])) {
        items.push(lines[i].slice(2));
        i++;
      }
      out.push(
        <ul key={key++} className="my-2 list-disc space-y-1 pl-5 text-sm leading-relaxed">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (/^>\s?/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        items.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      out.push(
        <blockquote
          key={key++}
          className="surface-glass my-2 rounded-md border-l-2 border-primary/60 px-3 py-2 text-sm italic leading-relaxed text-muted-foreground"
        >
          {items.map((it, ii) => (
            <p key={ii}>{renderInline(it)}</p>
          ))}
        </blockquote>,
      );
      continue;
    }
    if (line.trim() === "") {
      out.push(<div key={key++} className="h-2" />);
      i++;
      continue;
    }
    if (line.trim() === "---") {
      out.push(<Separator key={key++} className="my-4" />);
      i++;
      continue;
    }
    out.push(
      <p key={key++} className="my-1 text-sm leading-relaxed">
        {renderInline(line)}
      </p>,
    );
    i++;
  }
  return <div>{out}</div>;
}

function renderInline(line: string): React.ReactNode {
  // Process ``code`` first so subsequent regexes don't eat the backticks.
  const tokens: React.ReactNode[] = [];
  const codeRegex = /`([^`]+)`/g;
  let lastEnd = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = codeRegex.exec(line)) !== null) {
    if (match.index > lastEnd) {
      tokens.push(emphasizeText(line.slice(lastEnd, match.index), key++));
    }
    tokens.push(
      <code
        key={`c${key++}`}
        className="rounded bg-muted px-1 py-0.5 font-mono text-xs"
      >
        {match[1]}
      </code>,
    );
    lastEnd = match.index + match[0].length;
  }
  if (lastEnd < line.length) {
    tokens.push(emphasizeText(line.slice(lastEnd), key++));
  }
  return <>{tokens}</>;
}

function emphasizeText(text: string, key: number): React.ReactNode {
  const out: React.ReactNode[] = [];
  // Split on bold first (``**text**``) then italic (``_text_``).
  const boldRegex = /\*\*([^*]+)\*\*/g;
  let lastEnd = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = boldRegex.exec(text)) !== null) {
    if (m.index > lastEnd) {
      out.push(italicize(text.slice(lastEnd, m.index), `${key}-${k++}`));
    }
    out.push(
      <strong key={`b${key}-${k++}`} className="font-semibold text-foreground">
        {m[1]}
      </strong>,
    );
    lastEnd = m.index + m[0].length;
  }
  if (lastEnd < text.length) {
    out.push(italicize(text.slice(lastEnd), `${key}-${k++}`));
  }
  return <span key={`s${key}`}>{out}</span>;
}

function italicize(text: string, key: string): React.ReactNode {
  const out: React.ReactNode[] = [];
  const italicRegex = /_([^_]+)_/g;
  let lastEnd = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = italicRegex.exec(text)) !== null) {
    if (m.index > lastEnd) out.push(text.slice(lastEnd, m.index));
    out.push(
      <em key={`i${key}-${k++}`} className="italic">
        {m[1]}
      </em>,
    );
    lastEnd = m.index + m[0].length;
  }
  if (lastEnd < text.length) out.push(text.slice(lastEnd));
  return <span key={`t${key}`}>{out}</span>;
}

function DataRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className={mono ? "font-mono text-xs" : "text-sm font-medium"}>
        {value}
      </span>
    </div>
  );
}

function TranscriptTab({ id }: { id: string }) {
  const transcript = useQuery({
    queryKey: ["persona", id, "transcript"],
    queryFn: () => getPersonaTranscript(id),
    enabled: !!id,
  });

  if (transcript.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

  if (!transcript.data?.length) {
    return (
      <EmptyState
        icon={<MessageSquare className="h-5 w-5" />}
        title="No transcript on disk"
        description="Either this persona was created before transcripts were persisted, or the file was deleted."
      />
    );
  }

  return (
    <div className="space-y-3">
      {transcript.data.map((entry, idx) => (
        <Card key={idx} className="bg-card/60">
          <CardHeader className="flex-row items-start justify-between gap-3 pb-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline">{entry.dimension}</Badge>
              {entry.is_followup ? (
                <Badge variant="warning">follow-up</Badge>
              ) : null}
              {entry.is_holdout ? (
                <Badge variant="muted">holdout</Badge>
              ) : null}
            </div>
            <span className="font-mono text-xs text-muted-foreground">
              {formatRelative(entry.timestamp)}
            </span>
          </CardHeader>
          <CardContent className="space-y-3 pt-0">
            <div className="flex items-start gap-2 text-sm leading-relaxed">
              <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <p>{entry.question}</p>
            </div>
            <div className="flex items-start gap-2 rounded-md border border-border bg-background/40 p-3 text-sm leading-relaxed">
              <Quote className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <p className="whitespace-pre-wrap">{entry.answer}</p>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
