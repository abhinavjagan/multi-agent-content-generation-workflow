import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChartLine,
  CheckCircle2,
  Play,
  Plus,
  Square,
  Trash2,
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
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ApiError, getPersona, streamEval } from "@/lib/api";
import type { EvalScoreEvent, PostMode } from "@/lib/types";

const DEFAULTS: string[] = [
  "explain what idempotency means in API design",
  "share a hot take on remote work",
  "apologize for shipping a regression last week",
  "tell a small joke about deadlines",
  "disagree with the claim that microservices are always better",
  "explain why monitoring p99 latency matters",
  "share a quick tip on how to write better commit messages",
  "your view on rewriting legacy code vs. refactoring it",
];

interface Row {
  topic: string;
  status: "pending" | "running" | "done" | "error";
  score: number | null;
  violations: string[];
  posts: string[];
  error?: string;
}

export default function PersonaEval() {
  const { id = "" } = useParams<{ id: string }>();
  const persona = useQuery({
    queryKey: ["persona", id],
    queryFn: () => getPersona(id),
    enabled: !!id,
  });

  const [prompts, setPrompts] = useState<string[]>(DEFAULTS);
  const [mode, setMode] = useState<PostMode>("single");
  const [rows, setRows] = useState<Row[]>([]);
  const [running, setRunning] = useState(false);
  const [average, setAverage] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // When prompts change, reset rows so the table reflects the new plan.
  useEffect(() => {
    if (running) return;
    setRows(
      prompts.map((p) => ({
        topic: p,
        status: "pending",
        score: null,
        violations: [],
        posts: [],
      })),
    );
    setAverage(null);
  }, [prompts, running]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const start = async () => {
    const cleanPrompts = prompts.map((p) => p.trim()).filter(Boolean);
    if (!cleanPrompts.length) {
      toast.error("Add at least one prompt");
      return;
    }
    if (cleanPrompts.length > 64) {
      toast.error("Max 64 prompts per run");
      return;
    }
    setRunning(true);
    setAverage(null);
    setRows(
      cleanPrompts.map((topic, idx) => ({
        topic,
        status: idx === 0 ? "running" : "pending",
        score: null,
        violations: [],
        posts: [],
      })),
    );
    abortRef.current = new AbortController();
    let nextIdx = 0;
    try {
      await streamEval(
        id,
        { prompts: cleanPrompts, mode },
        {
          onScore: (event: EvalScoreEvent) => {
            setRows((current) => {
              const next = [...current];
              const i = next.findIndex(
                (r) => r.status !== "done" && r.status !== "error" && r.topic === event.topic,
              );
              const target = i >= 0 ? i : nextIdx;
              if (target < next.length) {
                next[target] = {
                  topic: event.topic,
                  status: event.error ? "error" : "done",
                  score: event.score ?? null,
                  violations: event.violations ?? [],
                  posts: event.posts ?? [],
                  error: event.error,
                };
              }
              if (target + 1 < next.length && next[target + 1].status === "pending") {
                next[target + 1] = { ...next[target + 1], status: "running" };
              }
              nextIdx = target + 1;
              return next;
            });
          },
          onDone: ({ average: avg }) => {
            setAverage(avg);
            setRunning(false);
          },
          onError: (err) => {
            toast.error(err.message, {
              description:
                "Eval stream interrupted. Verify Ollama is reachable in Settings and try again.",
            });
          },
          signal: abortRef.current.signal,
        },
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        toast.error(
          err instanceof ApiError ? err.detail : (err as Error).message,
          {
            description:
              "Couldn't start the eval. Check that the persona exists and Ollama is up.",
          },
        );
      }
      setRunning(false);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setRunning(false);
  };

  const updatePrompt = (idx: number, value: string) => {
    setPrompts((p) => p.map((x, i) => (i === idx ? value : x)));
  };
  const removePrompt = (idx: number) => {
    setPrompts((p) => p.filter((_, i) => i !== idx));
  };
  const addPrompt = () => {
    setPrompts((p) => [...p, ""]);
  };

  const completed = rows.filter((r) => r.status === "done");

  if (persona.isLoading) {
    return <Skeleton className="h-32 w-full" />;
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
    <div className="space-y-6 animate-fade-in">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to={`/personas/${id}`}>
            <ArrowLeft className="h-4 w-4" />
            Back to {persona.data.name}
          </Link>
        </Button>
        <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <ChartLine className="h-6 w-6 text-primary" />
          Evaluate {persona.data.name}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Generate a draft for each prompt, run the persona consistency critic,
          and stream the scores back live.
        </p>
      </div>

      <Card>
        <CardHeader className="border-b border-border pb-4">
          <CardTitle className="text-base">Prompts</CardTitle>
          <CardDescription>
            One topic per row. Defaults match the CLI's built-in battery.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 pt-4">
          {prompts.map((p, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <span className="w-6 font-mono text-xs text-muted-foreground">
                {idx + 1}.
              </span>
              <Input
                value={p}
                onChange={(e) => updatePrompt(idx, e.target.value)}
                placeholder="topic"
                disabled={running}
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => removePrompt(idx)}
                disabled={running}
                aria-label="Remove prompt"
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={addPrompt}
            disabled={running}
          >
            <Plus className="h-4 w-4" />
            Add prompt
          </Button>
        </CardContent>
        <CardFooter className="flex items-center justify-between gap-3 border-t border-border pt-4">
          <div className="flex items-center gap-2">
            <Label htmlFor="mode" className="text-xs">
              Mode
            </Label>
            <Select
              id="mode"
              value={mode}
              onChange={(e) => setMode(e.target.value as PostMode)}
              disabled={running}
              className="h-8 w-32 text-xs"
            >
              <option value="single">single</option>
              <option value="thread">thread</option>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            {running ? (
              <Button variant="destructive" onClick={stop}>
                <Square className="h-4 w-4" />
                Stop
              </Button>
            ) : (
              <Button onClick={start}>
                <Play className="h-4 w-4" />
                Run eval
              </Button>
            )}
          </div>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader className="border-b border-border pb-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Results</CardTitle>
              <CardDescription>
                {completed.length}/{rows.length} scored
                {average != null
                  ? ` · avg ${average.toFixed(2)}/5`
                  : ""}
              </CardDescription>
            </div>
            {average != null ? (
              <Badge
                variant={
                  average >= 4
                    ? "success"
                    : average >= 2
                      ? "warning"
                      : "destructive"
                }
              >
                <CheckCircle2 className="h-3 w-3" />
                avg {average.toFixed(2)}/5
              </Badge>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-card text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-3 text-left font-medium">Prompt</th>
                  <th className="px-4 py-3 text-center font-medium">Score</th>
                  <th className="px-4 py-3 text-left font-medium">Top violations</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => (
                  <EvalRow row={row} idx={idx} key={`${idx}-${row.topic}`} />
                ))}
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="px-4 py-6 text-center text-sm text-muted-foreground">
                      Add prompts above and click Run.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function EvalRow({ row, idx }: { row: Row; idx: number }) {
  const variant = useMemo<
    "success" | "warning" | "destructive" | "muted"
  >(() => {
    if (row.score == null) return "muted";
    if (row.score >= 4) return "success";
    if (row.score >= 2) return "warning";
    return "destructive";
  }, [row.score]);
  return (
    <tr className="border-t border-border">
      <td className="max-w-md px-4 py-3">
        <p className="font-medium">{row.topic}</p>
        {row.posts.length ? (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
            {row.posts[0]}
          </p>
        ) : null}
        {row.error ? (
          <p className="mt-1 text-xs text-destructive">{row.error}</p>
        ) : null}
      </td>
      <td className="px-4 py-3 text-center">
        {row.status === "running" ? (
          <span className="inline-flex h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        ) : row.status === "pending" ? (
          <span className="text-xs text-muted-foreground">…</span>
        ) : row.score == null ? (
          <Badge variant="destructive">err</Badge>
        ) : (
          <Badge variant={variant}>{row.score}/5</Badge>
        )}
      </td>
      <td className="px-4 py-3">
        {row.violations.length ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="line-clamp-1 cursor-help text-xs text-warning">
                {row.violations[0]}
                {row.violations.length > 1
                  ? ` · +${row.violations.length - 1} more`
                  : ""}
              </span>
            </TooltipTrigger>
            <TooltipContent side="left">
              <ul className="space-y-1">
                {row.violations.map((v, i) => (
                  <li key={i}>· {v}</li>
                ))}
              </ul>
            </TooltipContent>
          </Tooltip>
        ) : row.status === "done" ? (
          <span className="text-xs text-muted-foreground">—</span>
        ) : null}
        <span className="sr-only">row {idx}</span>
      </td>
    </tr>
  );
}
