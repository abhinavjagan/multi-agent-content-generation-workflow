import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChartLine,
  Edit3,
  Hammer,
  MessageSquare,
  Quote,
  Trash2,
  Wand2,
} from "lucide-react";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
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
  getPersonaTranscript,
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
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
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
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
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

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="transcript">Transcript</TabsTrigger>
          <TabsTrigger value="actions">Danger zone</TabsTrigger>
        </TabsList>

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
      { label: "Domains", items: spec.domains },
      { label: "Topics they love", items: spec.topics_loved },
      { label: "Topics they avoid", items: spec.topics_avoided },
      { label: "Signature phrases", items: spec.signature_phrases },
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
        </CardContent>
      </Card>
    </div>
  );
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
