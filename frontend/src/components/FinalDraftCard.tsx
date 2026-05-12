/**
 * Replaces the old "Posted to X" card.
 *
 * x-agent never publishes anywhere; on approval the user gets a
 * polished, copyable artifact and (optionally) a deep link into the X
 * compose dialog. Copy-all + per-tweet copy + intent URL handoff.
 */

import { useMemo, useState } from "react";
import { Check, CheckCircle2, Copy, ExternalLink, Send } from "lucide-react";
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
import { xIntentUrl } from "@/lib/recentDrafts";
import { cn } from "@/lib/utils";

export interface FinalDraftCardProps {
  posts: string[];
  topic: string;
  personaName?: string | null;
  onReset?: () => void;
}

export function FinalDraftCard({
  posts,
  topic,
  personaName,
  onReset,
}: FinalDraftCardProps) {
  const [copiedAll, setCopiedAll] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);

  const fullText = useMemo(() => posts.join("\n\n"), [posts]);
  const intentUrl = posts.length > 0 ? xIntentUrl(posts[0]) : null;

  const handleCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(fullText);
      setCopiedAll(true);
      setTimeout(() => setCopiedAll(false), 1400);
      toast.success("Copied all tweets to clipboard.");
    } catch {
      toast.error("Clipboard not available. Try selecting the text manually.");
    }
  };

  const handleCopyOne = async (idx: number) => {
    try {
      await navigator.clipboard.writeText(posts[idx]);
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx((cur) => (cur === idx ? null : cur)), 1200);
    } catch {
      toast.error("Clipboard not available. Try selecting the text manually.");
    }
  };

  return (
    <Card className="border-success/40 ring-1 ring-success/20">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-success" />
              Draft finalized
            </CardTitle>
            <CardDescription>
              {posts.length} {posts.length === 1 ? "tweet" : "tweets"} ready to
              copy
              {personaName ? (
                <>
                  {" "}· in <span className="font-medium">{personaName}</span>'s
                  voice
                </>
              ) : null}
              {topic ? (
                <>
                  {" "}· about{" "}
                  <span className="font-mono text-xs">"{truncate(topic, 60)}"</span>
                </>
              ) : null}
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={handleCopyAll}
            disabled={posts.length === 0}
          >
            {copiedAll ? (
              <Check className="h-4 w-4 text-success" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
            Copy all
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {posts.map((post, idx) => (
          <div
            key={idx}
            className={cn(
              "surface-glass rounded-xl p-3 text-sm",
            )}
          >
            <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
              <span>
                Tweet {idx + 1}/{posts.length} · {post.length} chars
              </span>
              <button
                type="button"
                onClick={() => handleCopyOne(idx)}
                className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                aria-label={`Copy tweet ${idx + 1}`}
                title="Copy"
              >
                {copiedIdx === idx ? (
                  <Check className="h-3.5 w-3.5 text-success" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
            <p className="whitespace-pre-wrap font-mono leading-relaxed">
              {post}
            </p>
          </div>
        ))}
      </CardContent>
      <CardFooter className="flex-wrap gap-2">
        {intentUrl ? (
          <Button asChild>
            <a
              href={intentUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="Opens X (Twitter) compose in a new tab with the first tweet pre-filled. We never post for you."
            >
              <Send className="h-4 w-4" />
              Open in X compose
              <ExternalLink className="ml-1 h-3.5 w-3.5 opacity-70" />
            </a>
          </Button>
        ) : null}
        {onReset ? (
          <Button variant="ghost" onClick={onReset}>
            Start a new draft
          </Button>
        ) : null}
        <p className="ml-auto text-[11px] text-muted-foreground">
          x-agent never posts. You copy it from here.
        </p>
      </CardFooter>
    </Card>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export default FinalDraftCard;
