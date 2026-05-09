import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Cpu,
  KeyRound,
  PenLine,
  Sparkles,
  Users,
  XCircle,
} from "lucide-react";
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { getHealth, listPersonas } from "@/lib/api";

export default function Dashboard() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const personas = useQuery({ queryKey: ["personas"], queryFn: listPersonas });

  const ollamaOk = health.data?.ollama.ok && health.data.ollama.has_configured_model;

  return (
    <div className="space-y-8 animate-fade-in">
      <section className="relative overflow-hidden rounded-2xl border border-border bg-card p-8 shadow-sm">
        <div className="grid gap-6 lg:grid-cols-[2fr,1fr] lg:items-end">
          <div className="space-y-3">
            <Badge variant="default" className="w-fit">
              <Sparkles className="h-3 w-3" />
              local-first
            </Badge>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              Draft, review, and post to X — with your own voice.
            </h1>
            <p className="max-w-2xl text-base text-muted-foreground">
              Generate posts with a local Ollama model, optionally as a saved
              persona. Every draft runs through a consistency critic, then
              pauses for your review before anything is published.
            </p>
            <div className="flex flex-wrap gap-2 pt-2">
              <Button asChild>
                <Link to="/draft">
                  <PenLine className="h-4 w-4" />
                  New draft
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </Button>
              <Button asChild variant="outline">
                <Link to="/personas/new">
                  <Users className="h-4 w-4" />
                  Create persona
                </Link>
              </Button>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-background/40 p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Quick status
            </p>
            <ul className="mt-3 space-y-2 text-sm">
              <li className="flex items-center justify-between">
                <span className="text-muted-foreground">Ollama</span>
                {health.isLoading ? (
                  <Skeleton className="h-4 w-16" />
                ) : ollamaOk ? (
                  <span className="font-mono text-xs text-success">
                    {health.data?.ollama.configured_model}
                  </span>
                ) : (
                  <span className="text-xs text-destructive">unreachable</span>
                )}
              </li>
              <li className="flex items-center justify-between">
                <span className="text-muted-foreground">X credentials</span>
                {health.isLoading ? (
                  <Skeleton className="h-4 w-16" />
                ) : health.data?.x.has_credentials ? (
                  <Badge variant="success">configured</Badge>
                ) : (
                  <Badge variant="muted">dry-run only</Badge>
                )}
              </li>
              <li className="flex items-center justify-between">
                <span className="text-muted-foreground">Personas</span>
                {personas.isLoading ? (
                  <Skeleton className="h-4 w-12" />
                ) : (
                  <span className="font-mono text-xs">
                    {personas.data?.length ?? 0}
                  </span>
                )}
              </li>
            </ul>
          </div>
        </div>
      </section>

      {health.data && !ollamaOk ? (
        <Alert variant="warning">
          <XCircle className="h-4 w-4" />
          <div className="space-y-1">
            <AlertTitle>Ollama is not ready.</AlertTitle>
            <AlertDescription>
              {health.data.ollama.error
                ? `Could not reach ${health.data.ollama.base_url}: ${health.data.ollama.error}`
                : `Model ${health.data.ollama.configured_model} is not pulled. Run:`}
              <pre className="mt-2 rounded-md bg-background/80 p-2 font-mono text-xs">
                {`ollama pull ${health.data.ollama.configured_model}`}
              </pre>
            </AlertDescription>
          </div>
        </Alert>
      ) : null}

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatusCard
          icon={<Cpu className="h-5 w-5" />}
          title="Generation model"
          value={health.data?.ollama.configured_model ?? "—"}
          hint={
            health.data?.ollama.has_configured_model
              ? "ready to draft"
              : "not pulled yet"
          }
          status={
            health.data?.ollama.has_configured_model ? "success" : "destructive"
          }
        />
        <StatusCard
          icon={<KeyRound className="h-5 w-5" />}
          title="X publishing"
          value={
            health.data?.x.has_credentials ? "OAuth 1.0a configured" : "no credentials"
          }
          hint={`max ${health.data?.x.max_tweet_chars ?? 275} chars per tweet`}
          status={health.data?.x.has_credentials ? "success" : "muted"}
        />
        <StatusCard
          icon={<Users className="h-5 w-5" />}
          title="Saved personas"
          value={`${personas.data?.length ?? 0}`}
          hint={
            health.data?.personas.dir
              ? health.data.personas.dir.replace(
                  health.data.personas.dir.split("/").slice(-3, -2)[0] ?? "",
                  health.data.personas.dir.split("/").slice(-3, -2)[0] ?? "",
                )
              : ""
          }
          status="default"
        />
      </section>

      <section>
        <Card>
          <CardHeader>
            <CardTitle>Recent personas</CardTitle>
            <CardDescription>
              Pick one to draft as, refine, or evaluate.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {personas.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : !personas.data?.length ? (
              <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                No personas yet.{" "}
                <Link to="/personas/new" className="text-primary underline-offset-2 hover:underline">
                  Create your first
                </Link>
                .
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {personas.data.slice(0, 5).map((p) => (
                  <li
                    key={p.id}
                    className="flex items-center justify-between gap-4 py-3"
                  >
                    <div>
                      <Link
                        to={`/personas/${p.id}`}
                        className="font-medium hover:underline"
                      >
                        {p.name}
                      </Link>
                      <p className="font-mono text-xs text-muted-foreground">
                        {p.id}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Badge variant={p.is_real_person ? "default" : "secondary"}>
                        {p.is_real_person ? "real" : "fictional"}
                      </Badge>
                      <Badge variant="outline">
                        {p.voice_brevity} · {p.voice_humor}
                      </Badge>
                      <Button variant="ghost" size="sm" asChild>
                        <Link to={`/draft?persona=${p.id}`}>
                          Draft as
                          <ArrowRight className="h-3 w-3" />
                        </Link>
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

interface StatusCardProps {
  icon: React.ReactNode;
  title: string;
  value: React.ReactNode;
  hint?: string;
  status?: "success" | "destructive" | "muted" | "default";
}

function StatusCard({ icon, title, value, hint, status = "default" }: StatusCardProps) {
  const ringMap: Record<string, string> = {
    success: "ring-success/35",
    destructive: "ring-destructive/40",
    muted: "ring-border",
    default: "ring-primary/30",
  };
  const iconMap: Record<string, string> = {
    success: "bg-success/15 text-success",
    destructive: "bg-destructive/15 text-destructive",
    muted: "bg-muted text-muted-foreground",
    default: "bg-primary/15 text-primary",
  };
  const Status = status === "success" ? CheckCircle2 : XCircle;
  return (
    <Card className={`ring-1 ring-inset ${ringMap[status]}`}>
      <CardHeader className="flex-row items-start justify-between gap-3 pb-2">
        <div className={`grid h-9 w-9 place-items-center rounded-md ${iconMap[status]}`}>
          {icon}
        </div>
        {status === "success" ? (
          <CheckCircle2 className="h-4 w-4 text-success" />
        ) : status === "destructive" ? (
          <Status className="h-4 w-4 text-destructive" />
        ) : null}
      </CardHeader>
      <CardContent>
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          {title}
        </p>
        <p className="mt-1 truncate text-lg font-semibold">{value}</p>
        {hint ? (
          <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
