import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  Edit3,
  MessageSquare,
  SkipForward,
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  ApiError,
  getPersona,
  refinePersona,
  refineQuestions,
} from "@/lib/api";
import type { QuestionBankEntry, RefineEntry } from "@/lib/types";

const DIMENSIONS = [
  "all",
  "style",
  "brevity",
  "humor",
  "values",
  "opinions",
  "boundaries",
  "banned_phrases",
  "signature_phrases",
  "domains",
  "topics_loved",
  "topics_avoided",
  "decision_style",
  "confidence_phrasing",
  "example_explainer",
  "example_disagreement",
  "example_apology",
];

export default function PersonaRefine() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const persona = useQuery({
    queryKey: ["persona", id],
    queryFn: () => getPersona(id),
    enabled: !!id,
  });

  const [dimension, setDimension] = useState("all");
  const [quick, setQuick] = useState(false);
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [draft, setDraft] = useState("");

  const questions = useQuery({
    queryKey: ["refine-questions", id, dimension, quick],
    queryFn: () =>
      refineQuestions(id, {
        dimension: dimension === "all" ? undefined : dimension,
        quick,
      }),
    enabled: !!id,
  });

  // Reset progress when scope changes.
  useEffect(() => {
    setStep(0);
    setAnswers({});
    setDraft("");
  }, [dimension, quick]);

  // Pre-fill draft area when revisiting a step.
  useEffect(() => {
    setDraft(answers[step] ?? "");
  }, [step, answers]);

  const refineMutation = useMutation({
    mutationFn: (entries: RefineEntry[]) => refinePersona(id, entries),
    onSuccess: (spec) => {
      toast.success("Persona refined");
      qc.setQueryData(["persona", id], spec);
      qc.invalidateQueries({ queryKey: ["personas"] });
      qc.invalidateQueries({ queryKey: ["persona", id, "transcript"] });
      navigate(`/personas/${id}`);
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message, {
        description:
          "Your draft answers are still in this form — fix the issue and resubmit. Nothing was lost.",
      });
    },
  });

  const total = questions.data?.length ?? 0;
  const collected = useMemo(
    () =>
      Object.entries(answers).filter(([, v]) => v && v.trim()).length,
    [answers],
  );
  const current: QuestionBankEntry | undefined = questions.data?.[step];

  if (persona.isLoading || questions.isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!persona.data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Persona not found</AlertTitle>
        <AlertDescription>{id}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 animate-fade-in">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to={`/personas/${id}`}>
            <ArrowLeft className="h-4 w-4" />
            Back to {persona.data.name}
          </Link>
        </Button>
        <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Edit3 className="h-6 w-6 text-primary" />
          Refine {persona.data.name}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Append new Q+A entries to the transcript and re-extract the spec.
          Existing answers are preserved.
        </p>
      </div>

      <Card>
        <CardHeader className="border-b border-border pb-4">
          <CardTitle className="text-base">Scope</CardTitle>
          <CardDescription>
            Pick the questions you want to (re-)answer.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 pt-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>Dimension</Label>
            <Select
              value={dimension}
              onChange={(e) => setDimension(e.target.value)}
            >
              {DIMENSIONS.map((d) => (
                <option key={d} value={d}>
                  {d === "all" ? "all questions" : d}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex items-end justify-between gap-3 rounded-md border border-border p-3">
            <div className="space-y-1">
              <Label htmlFor="refine-quick" className="cursor-pointer">
                Quick mode
              </Label>
              <p className="text-xs text-muted-foreground">
                Smaller set (~6 questions). Ignored when a single dimension is
                selected.
              </p>
            </div>
            <Switch
              id="refine-quick"
              checked={quick}
              onCheckedChange={setQuick}
              disabled={dimension !== "all"}
            />
          </div>
        </CardContent>
      </Card>

      {total === 0 ? (
        <EmptyQuestions />
      ) : (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div>
                <CardTitle className="flex items-center gap-2 text-base">
                  <MessageSquare className="h-5 w-5 text-primary" />
                  Question {step + 1} of {total}
                </CardTitle>
                <CardDescription className="font-mono text-xs">
                  {current?.dimension}
                  {current?.kind === "generative" ? " · writing sample" : ""}
                </CardDescription>
              </div>
              <Badge variant="outline">{collected} answered</Badge>
            </div>
            <Progress
              value={((step + 1) / total) * 100}
              className="mt-3"
            />
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="rounded-md bg-secondary/60 p-3 text-sm leading-relaxed">
              {current?.prompt}
            </p>
            <Textarea
              autoSize
              rows={5}
              maxLength={20_000}
              value={draft}
              placeholder={
                current?.kind === "generative"
                  ? "Write a short post in their real voice…"
                  : "Type their answer. Skip to leave blank."
              }
              onChange={(e) => setDraft(e.target.value)}
            />
          </CardContent>
          <CardFooter className="flex items-center justify-between gap-3">
            <div className="flex gap-2">
              <Button
                variant="ghost"
                disabled={step === 0}
                onClick={() => setStep(Math.max(0, step - 1))}
              >
                Previous
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setAnswers((a) => ({ ...a, [step]: "" }));
                  setStep(Math.min(total - 1, step + 1));
                }}
              >
                <SkipForward className="h-4 w-4" />
                Skip
              </Button>
            </div>
            <div className="flex gap-2">
              {step < total - 1 ? (
                <Button
                  disabled={!draft.trim()}
                  onClick={() => {
                    setAnswers((a) => ({ ...a, [step]: draft.trim() }));
                    setStep(step + 1);
                  }}
                >
                  Save & next
                </Button>
              ) : (
                <Button
                  variant="outline"
                  disabled={!draft.trim()}
                  onClick={() =>
                    setAnswers((a) => ({ ...a, [step]: draft.trim() }))
                  }
                >
                  Save answer
                </Button>
              )}
              <Button
                loading={refineMutation.isPending}
                disabled={
                  collected === 0 && !draft.trim()
                }
                onClick={() => {
                  const final = { ...answers };
                  if (draft.trim()) final[step] = draft.trim();
                  const entries: RefineEntry[] = Object.entries(final)
                    .map(([k, v]) => {
                      const q = questions.data?.[Number(k)];
                      if (!q || !v?.trim()) return null;
                      return {
                        dimension: q.dimension,
                        question: q.prompt,
                        answer: v.trim(),
                      };
                    })
                    .filter((x): x is RefineEntry => x !== null);
                  if (!entries.length) {
                    toast.error("Answer at least one question first");
                    return;
                  }
                  refineMutation.mutate(entries);
                }}
              >
                <CheckCircle2 className="h-4 w-4" />
                Submit refine
              </Button>
            </div>
          </CardFooter>
        </Card>
      )}
    </div>
  );
}

function EmptyQuestions() {
  return (
    <Alert variant="warning">
      <AlertTitle>No questions in this scope</AlertTitle>
      <AlertDescription>
        Pick a different dimension or turn off quick mode.
      </AlertDescription>
    </Alert>
  );
}
