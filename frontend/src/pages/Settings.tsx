import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Copy,
  Globe,
  Lock,
  RefreshCw,
  Server,
  Settings as SettingsIcon,
  Sparkles,
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
import { Separator } from "@/components/ui/separator";
import { getHealth } from "@/lib/api";
import type { OllamaStatus, ResearchConfig } from "@/lib/types";

export default function Settings() {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-sm text-muted-foreground">
            Read-only view of the running config. Secrets are never returned by
            the API.
          </p>
        </div>
        <Button
          variant="outline"
          loading={health.isFetching}
          onClick={() => qc.invalidateQueries({ queryKey: ["health"] })}
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </Button>
      </div>

      {health.isLoading || !health.data ? (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <OllamaCard health={health.data.ollama} />
          <OutputCard maxChars={health.data.config.max_tweet_chars} />
          <CriticCard
            criticMin={health.data.config.critic_min_score}
            criticMax={health.data.config.critic_max_attempts}
            topK={health.data.config.persona_top_k}
            criticModel={health.data.ollama.critic_model}
            embeddingModel={health.data.ollama.embedding_model}
          />
          <ResearchCard research={health.data.config.research} />
          <PathsCard
            personaDir={health.data.personas.dir}
            personaCount={health.data.personas.count}
            version={health.data.version}
          />
        </div>
      )}
    </div>
  );
}

function ResearchCard({ research }: { research: ResearchConfig }) {
  const usingFallback =
    research.preference === "auto" &&
    research.active_provider === "duckduckgo" &&
    !research.has_tavily_key &&
    !research.has_brave_key;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Globe className="h-5 w-5 text-primary" />
              Web research
            </CardTitle>
            <CardDescription>
              Optional grounding for the draft agent. Off until enabled per request.
            </CardDescription>
          </div>
          <Badge variant="muted">{research.active_provider}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Field
          label="Provider preference"
          value={research.preference}
          mono
          hint="auto = pick Tavily/Brave when keyed, else DuckDuckGo."
        />
        <div className="flex flex-wrap gap-1.5">
          <Badge variant={research.has_tavily_key ? "success" : "muted"}>
            {research.has_tavily_key ? "Tavily key set" : "Tavily key missing"}
          </Badge>
          <Badge variant={research.has_brave_key ? "success" : "muted"}>
            {research.has_brave_key ? "Brave key set" : "Brave key missing"}
          </Badge>
        </div>
        <Separator />
        <Field
          label="Max results"
          value={String(research.max_results)}
          hint="Hard cap on URLs/search hits per research call."
        />
        <Field
          label="Per-fetch timeout"
          value={`${research.fetch_timeout_s.toFixed(1)}s`}
        />
        <Field
          label="Content cap per source"
          value={`${research.max_content_chars.toLocaleString()} chars`}
        />
        <Alert variant="warning">
          <AlertTitle>Outbound network</AlertTitle>
          <AlertDescription>
            Enabling research is the only feature that sends data outside your
            machine. The chosen provider receives your query / URL list, and the
            target pages are fetched directly from this process.
            {usingFallback
              ? " Add TAVILY_API_KEY or BRAVE_SEARCH_API_KEY to .env for higher-quality search."
              : ""}
          </AlertDescription>
        </Alert>
      </CardContent>
    </Card>
  );
}

function OllamaCard({ health: h }: { health: OllamaStatus }) {
  const [copied, setCopied] = useState(false);
  const ok = h.ok && h.has_configured_model;
  const copyPull = async () => {
    try {
      await navigator.clipboard.writeText(`ollama pull ${h.configured_model}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Could not copy");
    }
  };
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Server className="h-5 w-5 text-primary" />
              Ollama
            </CardTitle>
            <CardDescription className="font-mono text-xs">
              {h.base_url}
            </CardDescription>
          </div>
          {ok ? (
            <Badge variant="success">
              <CheckCircle2 className="h-3 w-3" />
              ready
            </Badge>
          ) : (
            <Badge variant="destructive">
              <XCircle className="h-3 w-3" />
              {h.ok ? "model not pulled" : "unreachable"}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Field label="Generation model" value={h.configured_model} mono />
        <Field label="Embedding model" value={h.embedding_model} mono />
        <Field label="Critic model" value={h.critic_model} mono />
        {h.error ? (
          <Alert variant="destructive">
            <AlertTitle>Connection error</AlertTitle>
            <AlertDescription className="font-mono text-xs">
              {h.error}
            </AlertDescription>
          </Alert>
        ) : !h.has_configured_model ? (
          <Alert variant="warning">
            <AlertTitle>Model not pulled</AlertTitle>
            <AlertDescription>
              Run this in your terminal then click refresh:
              <div className="mt-2 flex items-center gap-2">
                <code className="flex-1 rounded-md bg-background/80 p-2 font-mono text-xs">
                  ollama pull {h.configured_model}
                </code>
                <Button size="sm" variant="outline" onClick={copyPull}>
                  {copied ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                  Copy
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        ) : null}
        <Separator />
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Available models
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {h.available_models.length ? (
              h.available_models.map((m) => (
                <Badge
                  key={m}
                  variant={m === h.configured_model ? "default" : "outline"}
                >
                  {m}
                </Badge>
              ))
            ) : (
              <span className="text-xs text-muted-foreground">
                None pulled.
              </span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function OutputCard({ maxChars }: { maxChars: number }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Lock className="h-5 w-5 text-primary" />
              Output mode
            </CardTitle>
            <CardDescription>
              Local-first: x-agent never posts on your behalf.
            </CardDescription>
          </div>
          <Badge variant="success">local-only</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Field
          label="Max tweet chars"
          value={String(maxChars)}
          mono
          hint="Cap each formatted post at this many characters. Drafts that overflow are split into a numbered thread."
        />
        <Alert>
          <AlertTitle>You publish, x-agent doesn't</AlertTitle>
          <AlertDescription>
            After approval the UI shows the finalized thread with copy-all,
            copy-each, and an <span className="font-mono">x.com/intent/tweet</span>
            {" "}deep link. The deep link prefills X's own composer in a new tab so
            you hit publish from your account, in your browser.
          </AlertDescription>
        </Alert>
      </CardContent>
    </Card>
  );
}

function CriticCard({
  criticMin,
  criticMax,
  topK,
  criticModel,
  embeddingModel,
}: {
  criticMin: number;
  criticMax: number;
  topK: number;
  criticModel: string;
  embeddingModel: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="h-5 w-5 text-primary" />
          Persona critic
        </CardTitle>
        <CardDescription>
          Tunables for the consistency loop in <code>build_graph()</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Field
          label="Critic min score"
          value={`${criticMin}/5`}
          hint="Drafts below this score loop back to generate_draft."
        />
        <Field
          label="Critic max attempts"
          value={String(criticMax)}
          hint="Hard cap on critic-driven regenerations per draft."
        />
        <Field
          label="Persona top-k"
          value={String(topK)}
          hint="Transcript chunks retrieved per draft."
        />
        <Field label="Critic model" value={criticModel} mono />
        <Field label="Embedding model" value={embeddingModel} mono />
      </CardContent>
    </Card>
  );
}

function PathsCard({
  personaDir,
  personaCount,
  version,
}: {
  personaDir: string;
  personaCount: number;
  version: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <SettingsIcon className="h-5 w-5 text-primary" />
          Storage
        </CardTitle>
        <CardDescription>Where the agent persists data.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Field label="Persona dir" value={personaDir} mono />
        <Field label="Personas saved" value={String(personaCount)} />
        <Separator />
        <Field label="Version" value={version} mono />
      </CardContent>
    </Card>
  );
}

function Field({
  label,
  value,
  mono,
  hint,
}: {
  label: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <span
          className={
            mono
              ? "rounded bg-secondary px-1.5 py-0.5 font-mono text-xs"
              : "text-sm font-medium"
          }
        >
          {value}
        </span>
      </div>
      {hint ? (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}
