import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Megaphone,
  MessageSquare,
  ShieldCheck,
  SkipForward,
  Sparkles,
  Wand2,
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
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  ApiError,
  startInterview,
  submitAnswer,
} from "@/lib/api";
import type { InterviewState } from "@/lib/types";

interface ConsentForm {
  name: string;
  isReal: boolean;
  handle: string;
  disclosure: string;
  quick: boolean;
  consentAck: boolean;
}

type Stage =
  | { kind: "consent" }
  | { kind: "interview"; state: InterviewState }
  | { kind: "done"; state: InterviewState };

export default function PersonaCreate() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [form, setForm] = useState<ConsentForm>({
    name: "",
    isReal: true,
    handle: "",
    disclosure: "",
    quick: false,
    consentAck: false,
  });
  const [stage, setStage] = useState<Stage>({ kind: "consent" });
  const [answer, setAnswer] = useState("");

  // Auto-fill the disclosure tag from the handle, but only while the user
  // hasn't typed their own custom tag.
  useEffect(() => {
    if (!form.isReal) return;
    const auto = form.handle
      ? `[AI persona of @${form.handle.replace(/^@/, "")}]`
      : form.name
        ? `[AI persona of ${form.name}]`
        : "";
    setForm((f) =>
      !f.disclosure || f.disclosure.startsWith("[AI persona of ")
        ? { ...f, disclosure: auto }
        : f,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.handle, form.name, form.isReal]);

  const startMutation = useMutation({
    mutationFn: () =>
      startInterview({
        name: form.name.trim(),
        is_real_person: form.isReal,
        disclosure_text: form.isReal ? form.disclosure.trim() : "",
        consent_ack: form.isReal ? form.consentAck : false,
        quick: form.quick,
      }),
    onSuccess: (state) => {
      if (state.error) {
        toast.error(state.error);
      }
      setStage(
        state.saved ? { kind: "done", state } : { kind: "interview", state },
      );
      setAnswer("");
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
    },
  });

  const answerMutation = useMutation({
    mutationFn: (input: { threadId: string; answer: string }) =>
      submitAnswer(input.threadId, input.answer),
    onSuccess: (state) => {
      if (state.error) toast.error(state.error);
      if (state.saved) {
        qc.invalidateQueries({ queryKey: ["personas"] });
        qc.invalidateQueries({ queryKey: ["health"] });
        setStage({ kind: "done", state });
      } else {
        setStage({ kind: "interview", state });
      }
      setAnswer("");
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message);
    },
  });

  if (stage.kind === "done") {
    return (
      <div className="mx-auto max-w-xl animate-fade-in">
        <Card className="border-success/40 bg-success/5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-success" />
              Persona saved
            </CardTitle>
            <CardDescription>
              Spec, transcript, and embeddings are persisted under{" "}
              <span className="font-mono">~/.x-agent/personas</span>.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-md border border-border bg-background/60 p-3 font-mono text-xs">
              {stage.state.persona_id}
            </div>
            <p className="text-sm text-muted-foreground">
              You can refine, evaluate, or draft as this persona now.
            </p>
          </CardContent>
          <CardFooter className="gap-2">
            <Button asChild variant="outline">
              <Link to="/personas">Back to personas</Link>
            </Button>
            <Button asChild>
              <Link to={`/draft?persona=${stage.state.persona_id}`}>
                <Wand2 className="h-4 w-4" />
                Draft as this persona
              </Link>
            </Button>
            <Button
              variant="ghost"
              onClick={() => navigate(`/personas/${stage.state.persona_id}`)}
            >
              View persona
            </Button>
          </CardFooter>
        </Card>
      </div>
    );
  }

  if (stage.kind === "interview") {
    const { state } = stage;
    const q = state.question;
    if (!q) {
      return (
        <Alert variant="warning" className="animate-fade-in">
          <Megaphone className="h-4 w-4" />
          <div>
            <AlertTitle>No question available.</AlertTitle>
            <AlertDescription>
              The server returned no pending question. Try refreshing and
              starting again.
            </AlertDescription>
          </div>
        </Alert>
      );
    }
    const total = state.total || 1;
    const idx = state.question_index;
    const pct = ((idx + 1) / total) * 100;
    const submitDisabled = answerMutation.isPending;

    return (
      <div className="mx-auto max-w-3xl space-y-4 animate-fade-in">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              Persona interview
            </p>
            <h1 className="text-xl font-semibold tracking-tight">
              Question {idx + 1} of {total}
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline">{q.dimension}</Badge>
            {q.kind === "generative" ? (
              <Badge variant="default">writing sample</Badge>
            ) : null}
            {q.is_followup ? <Badge variant="warning">follow-up</Badge> : null}
          </div>
        </div>
        <Progress value={pct} />
        <Card>
          <CardHeader>
            <CardTitle className="flex items-start gap-3 text-base font-medium leading-relaxed">
              <MessageSquare className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
              <span>{q.prompt}</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Textarea
              autoSize
              autoFocus
              rows={6}
              maxLength={20_000}
              value={answer}
              placeholder={
                q.kind === "generative"
                  ? "Write a short post in your real voice (2-4 sentences)…"
                  : "Type your answer. Be specific. Skip if you'd rather move on."
              }
              onChange={(e) => setAnswer(e.target.value)}
              disabled={submitDisabled}
            />
          </CardContent>
          <CardFooter className="justify-end gap-2">
            <Button
              variant="ghost"
              onClick={() =>
                answerMutation.mutate({
                  threadId: state.thread_id,
                  answer: "",
                })
              }
              disabled={submitDisabled}
            >
              <SkipForward className="h-4 w-4" />
              Skip
            </Button>
            <Button
              loading={answerMutation.isPending}
              disabled={!answer.trim() || submitDisabled}
              onClick={() =>
                answerMutation.mutate({
                  threadId: state.thread_id,
                  answer: answer.trim(),
                })
              }
            >
              Submit answer
            </Button>
          </CardFooter>
        </Card>
        <p className="text-center text-xs text-muted-foreground">
          You can leave this page; in-flight interviews use an in-memory
          checkpointer and won't survive a server restart.
        </p>
      </div>
    );
  }

  // Stage: consent
  const submitDisabled =
    !form.name.trim() ||
    (form.isReal && (!form.consentAck || !form.disclosure.trim())) ||
    startMutation.isPending;

  return (
    <div className="mx-auto max-w-2xl space-y-4 animate-fade-in">
      <div>
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          New persona
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">
          Capture a voice via conversation
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The agent walks you through ~17 (or 6 in quick mode) questions and
          then extracts a structured persona spec from your answers.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-5 w-5 text-primary" />
            Subject details
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Display name</Label>
            <Input
              id="name"
              value={form.name}
              maxLength={120}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Abhi"
              autoFocus
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex items-end justify-between gap-3 rounded-md border border-border p-3">
              <div className="space-y-1">
                <Label htmlFor="real" className="cursor-pointer">
                  Real person
                </Label>
                <p className="text-xs text-muted-foreground">
                  Adds consent + auto-disclosure to every post.
                </p>
              </div>
              <Switch
                id="real"
                checked={form.isReal}
                onCheckedChange={(v) => setForm({ ...form, isReal: v })}
              />
            </div>
            <div className="flex items-end justify-between gap-3 rounded-md border border-border p-3">
              <div className="space-y-1">
                <Label htmlFor="quick" className="cursor-pointer">
                  Quick mode
                </Label>
                <p className="text-xs text-muted-foreground">
                  6 questions instead of ~17.
                </p>
              </div>
              <Switch
                id="quick"
                checked={form.quick}
                onCheckedChange={(v) => setForm({ ...form, quick: v })}
              />
            </div>
          </div>

          {form.isReal ? (
            <div className="space-y-4 rounded-md border border-warning/30 bg-warning/5 p-4">
              <div className="flex items-start gap-3">
                <Megaphone className="mt-0.5 h-4 w-4 text-warning" />
                <p className="text-sm text-warning-foreground/90">
                  By continuing you confirm the subject has agreed to
                  participate, and that any generated posts will carry an
                  AI-persona disclosure.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="handle">X handle (optional)</Label>
                  <Input
                    id="handle"
                    value={form.handle}
                    placeholder="abhi"
                    maxLength={60}
                    onChange={(e) =>
                      setForm({ ...form, handle: e.target.value })
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="disclosure">Disclosure tag</Label>
                  <Input
                    id="disclosure"
                    value={form.disclosure}
                    placeholder="[AI persona of @handle]"
                    maxLength={120}
                    onChange={(e) =>
                      setForm({ ...form, disclosure: e.target.value })
                    }
                  />
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.consentAck}
                  onChange={(e) =>
                    setForm({ ...form, consentAck: e.target.checked })
                  }
                  className="h-4 w-4 rounded border-border bg-background accent-primary"
                />
                I have the subject's explicit consent.
              </label>
            </div>
          ) : null}
        </CardContent>
        <CardFooter className="justify-end gap-2">
          <Button asChild variant="ghost">
            <Link to="/personas">Cancel</Link>
          </Button>
          <Button
            loading={startMutation.isPending}
            disabled={submitDisabled}
            onClick={() => startMutation.mutate()}
          >
            <Sparkles className="h-4 w-4" />
            Start interview
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
