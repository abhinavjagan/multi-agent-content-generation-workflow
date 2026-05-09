/**
 * Render a list of web research results as compact, expandable cards.
 *
 * Used in three places on /draft:
 *  1. The research preview panel before generating ("Preview sources").
 *  2. Under the review card, so the reviewer can see what the agent saw.
 *  3. Under the variants card, where all variants share the same sources.
 *
 * Cards show only the title + hostname by default; the snippet/extracted
 * text is hidden behind a `<details>` so the layout stays tight when
 * there are 4+ sources.
 */

import { ExternalLink, Globe } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { WebResult } from "@/lib/types";

interface SourceListProps {
  results: WebResult[];
  /** Optional title -- defaults to "Sources (N)". */
  label?: string;
  /** Provider name to show as a badge (when not already on each result). */
  provider?: string | null;
  /** Tighter padding for use inside variant/review cards. */
  compact?: boolean;
}

function hostnameOf(raw: string): string {
  try {
    return new URL(raw).hostname.replace(/^www\./, "");
  } catch {
    return raw;
  }
}

export function SourceList({
  results,
  label,
  provider,
  compact = false,
}: SourceListProps) {
  if (!results || results.length === 0) return null;
  const heading = label ?? `Sources (${results.length})`;
  const headerProvider =
    provider ?? results.find((r) => r.provider)?.provider ?? null;

  return (
    <details
      className={
        compact
          ? "group rounded-md border border-border/60 bg-background/40 p-2.5"
          : "group rounded-lg border border-border bg-background/40 p-3"
      }
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-xs">
        <span className="flex items-center gap-1.5 font-medium">
          <Globe className="h-3.5 w-3.5 text-primary" />
          {heading}
        </span>
        <span className="flex items-center gap-1.5">
          {headerProvider ? (
            <Badge variant="muted" className="text-[10px]">
              {headerProvider}
            </Badge>
          ) : null}
          <span className="text-muted-foreground transition group-open:rotate-180">
            ▾
          </span>
        </span>
      </summary>
      <ul className={compact ? "mt-2 space-y-1.5" : "mt-3 space-y-2"}>
        {results.map((r, i) => {
          const host = hostnameOf(r.url);
          const body = (r.snippet || r.content || "").trim();
          const trimmed =
            body.length > 280 ? `${body.slice(0, 279)}\u2026` : body;
          return (
            <li
              key={`${i}-${r.url}`}
              className={
                compact
                  ? "rounded border border-border/50 bg-background/60 p-2 text-xs"
                  : "rounded-md border border-border/70 bg-background/60 p-3 text-sm"
              }
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">
                    {r.title || host || "(untitled)"}
                  </p>
                  <p className="truncate font-mono text-[11px] text-muted-foreground">
                    {host}
                    {r.source === "fetched" ? " · fetched" : " · search"}
                    {typeof r.score === "number"
                      ? ` · score ${r.score.toFixed(2)}`
                      : ""}
                  </p>
                </div>
                <a
                  href={r.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
                  aria-label="Open source"
                  title="Open in new tab"
                  onClick={(e) => e.stopPropagation()}
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              </div>
              {trimmed ? (
                <p
                  className={
                    compact
                      ? "mt-1 line-clamp-3 text-[11px] text-muted-foreground"
                      : "mt-1.5 line-clamp-4 text-xs text-muted-foreground"
                  }
                >
                  {trimmed}
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
