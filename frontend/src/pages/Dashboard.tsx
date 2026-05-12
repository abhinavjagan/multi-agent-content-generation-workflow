import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Copy,
  Cpu,
  PenLine,
  Send,
  Sparkles,
  Users,
  XCircle,
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { CoolShape } from "@/components/decor/CoolShape";
import { getHealth, listPersonas } from "@/lib/api";
import {
  type RecentDraft,
  listRecentDrafts,
  xIntentUrl,
} from "@/lib/recentDrafts";

export default function Dashboard() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const personas = useQuery({ queryKey: ["personas"], queryFn: listPersonas });
  const recent = useRecentDrafts();

  const ollamaOk =
    health.data?.ollama.ok && health.data.ollama.has_configured_model;

  return (
    <div className="space-y-8 animate-fade-in">
      <section className="surface-glass relative overflow-hidden rounded-2xl p-8">
        <div
          aria-hidden
          className="coolshape-blur pointer-events-none absolute -top-16 -right-12"
        >
          <CoolShape
            kind="star"
            fromColor="hsl(248 92% 65%)"
            toColor="hsl(322 88% 62%)"
            size={260}
          />
        </div>
        <div
          aria-hidden
          className="coolshape-blur pointer-events-none absolute -bottom-16 left-1/2"
        >
          <CoolShape
            kind="blob"
            fromColor="hsl(195 95% 55%)"
            toColor="hsl(322 88% 62%)"
            size={300}
          />
        </div>

        <div className="relative grid gap-6 lg:grid-cols-[2fr,1fr] lg:items-end">
          <div className="space-y-3">
            <Badge variant="default" className="w-fit">
              <Sparkles className="h-3 w-3" />
              local-first · never publishes
            </Badge>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              Draft posts in{" "}
              <span className="text-gradient-pop">your voice</span>. Copy. Done.
            </h1>
            <p className="max-w-2xl text-base text-muted-foreground">
              Generate posts locally with Ollama, shaped by a saved persona's
              personality profile. Every draft pauses for review, then hands you
              a polished artifact and a one-click deep link into X compose. We
              never log into anything.
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

          <div className="surface-glass-strong rounded-xl p-4">
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
                <span className="text-muted-foreground">Personas</span>
                {personas.isLoading ? (
                  <Skeleton className="h-4 w-12" />
                ) : (
                  <span className="font-mono text-xs">
                    {personas.data?.length ?? 0}
                  </span>
                )}
              </li>
              <li className="flex items-center justify-between">
                <span className="text-muted-foreground">Recent drafts</span>
                <span className="font-mono text-xs">{recent.length}</span>
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

      <section className="grid gap-4 sm:grid-cols-3">
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
          icon={<Users className="h-5 w-5" />}
          title="Saved personas"
          value={`${personas.data?.length ?? 0}`}
          hint={
            personas.data?.length
              ? "click below to draft or refine one"
              : "create one to clone a voice"
          }
          status={personas.data?.length ? "success" : "muted"}
        />
        <StatusCard
          icon={<PenLine className="h-5 w-5" />}
          title="Recent drafts"
          value={`${recent.length}`}
          hint={
            recent.length
              ? "kept locally; never uploaded anywhere"
              : "draft something to see it here"
          }
          status={recent.length ? "default" : "muted"}
        />
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent drafts</CardTitle>
            <CardDescription>
              Locally finalized drafts. Re-copy or open directly in X compose.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {recent.length === 0 ? (
              <div className="rounded-md border border-dashed border-border/60 bg-background/30 p-6 text-center text-sm text-muted-foreground">
                No drafts yet.{" "}
                <Link
                  to="/draft"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  Compose one
                </Link>
                .
              </div>
            ) : (
              <ul className="space-y-3">
                {recent.slice(0, 5).map((draft) => (
                  <RecentDraftRow key={draft.id} draft={draft} />
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

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
              <div className="rounded-md border border-dashed border-border/60 bg-background/30 p-6 text-center text-sm text-muted-foreground">
                No personas yet.{" "}
                <Link
                  to="/personas/new"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  Create your first
                </Link>
                .
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
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

function RecentDraftRow({ draft }: { draft: RecentDraft }) {
  const preview = (draft.posts[0] ?? "").slice(0, 140);
  const fullText = draft.posts.join("\n\n");

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(fullText);
      toast.success("Copied to clipboard.");
    } catch {
      toast.error("Clipboard not available.");
    }
  };

  const intent = draft.posts[0] ? xIntentUrl(draft.posts[0]) : null;

  return (
    <li className="surface-glass rounded-xl p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium" title={draft.topic}>
            {draft.topic || "(untitled)"}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {new Date(draft.finalized_at).toLocaleString()} · {draft.posts.length}{" "}
            tweet{draft.posts.length === 1 ? "" : "s"} · {draft.mode}
            {draft.persona_name ? ` · as ${draft.persona_name}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleCopy}
            title="Copy all tweets"
          >
            <Copy className="h-3.5 w-3.5" />
          </Button>
          {intent ? (
            <Button asChild variant="outline" size="sm">
              <a
                href={intent}
                target="_blank"
                rel="noopener noreferrer"
                title="Open the first tweet in X compose"
              >
                <Send className="h-3.5 w-3.5" />
              </a>
            </Button>
          ) : null}
        </div>
      </div>
      <p className="mt-2 line-clamp-2 whitespace-pre-wrap font-mono text-xs text-muted-foreground">
        {preview}
        {(draft.posts[0]?.length ?? 0) > 140 ? "…" : ""}
      </p>
    </li>
  );
}

function useRecentDrafts(): RecentDraft[] {
  const [drafts, setDrafts] = useState<RecentDraft[]>(() => listRecentDrafts());
  useEffect(() => {
    const handler = () => setDrafts(listRecentDrafts());
    window.addEventListener("x-agent:recent-drafts", handler);
    window.addEventListener("storage", handler);
    return () => {
      window.removeEventListener("x-agent:recent-drafts", handler);
      window.removeEventListener("storage", handler);
    };
  }, []);
  return drafts;
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
