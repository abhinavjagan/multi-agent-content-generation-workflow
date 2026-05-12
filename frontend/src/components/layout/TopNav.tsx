import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  CircleDot,
  Settings as SettingsIcon,
  Users,
  Wand2,
} from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";
import { Badge } from "@/components/ui/badge";
import { getHealth } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV: { to: string; label: string; icon: typeof Activity; end?: boolean }[] =
  [
    { to: "/", label: "Dashboard", icon: Activity, end: true },
    { to: "/draft", label: "Draft", icon: Wand2 },
    { to: "/personas", label: "Personas", icon: Users },
    { to: "/settings", label: "Settings", icon: SettingsIcon },
  ];

export function TopNav() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 15_000,
    staleTime: 5_000,
  });

  const ollamaOk = health.data?.ollama.ok && health.data.ollama.has_configured_model;
  const personaCount = health.data?.personas.count ?? 0;

  return (
    <header className="surface-glass sticky top-0 z-30 border-b border-border/70 backdrop-blur">
      <div className="container flex h-14 items-center gap-6">
        <NavLink to="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span
            className="grid h-7 w-7 place-items-center rounded-md text-primary-foreground shadow-[0_4px_14px_-2px_hsl(var(--accent-pink)/0.55)]"
            style={{
              background:
                "conic-gradient(from 215deg at 50% 50%, hsl(248 92% 62%), hsl(322 88% 62%), hsl(195 95% 55%), hsl(248 92% 62%))",
            }}
            aria-hidden
          >
            <span className="text-[15px] leading-none">×</span>
          </span>
          <span className="hidden bg-gradient-to-r from-primary via-pink-500 to-cyan-400 bg-clip-text text-transparent sm:inline">
            x-agent
          </span>
        </NavLink>

        <nav className="flex items-center gap-0.5">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary/15 text-primary shadow-[inset_0_-2px_0_0_hsl(var(--primary))]"
                    : "text-muted-foreground hover:bg-primary/10 hover:text-primary",
                )
              }
            >
              <Icon className="h-4 w-4" />
              <span className="hidden sm:inline">{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <div className="hidden gap-1.5 md:flex">
            <Badge
              variant={ollamaOk ? "success" : "destructive"}
              title={
                health.data?.ollama.error ??
                `Ollama ${health.data?.ollama.configured_model ?? ""}`
              }
            >
              <CircleDot className="h-3 w-3" />
              Ollama
            </Badge>
            <Badge
              variant={personaCount > 0 ? "default" : "muted"}
              title={`${personaCount} persona${personaCount === 1 ? "" : "s"} saved`}
            >
              <CircleDot className="h-3 w-3" />
              {personaCount} persona{personaCount === 1 ? "" : "s"}
            </Badge>
          </div>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
