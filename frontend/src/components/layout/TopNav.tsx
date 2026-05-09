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
  const xOk = health.data?.x.has_credentials;

  return (
    <header className="sticky top-0 z-30 border-b border-border/70 bg-background/85 backdrop-blur">
      <div className="container flex h-14 items-center gap-6">
        <NavLink to="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground shadow-sm">
            <span className="text-[15px] leading-none">×</span>
          </span>
          <span className="hidden sm:inline">x-agent</span>
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
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
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
              variant={xOk ? "success" : "muted"}
              title={
                xOk
                  ? "X credentials configured"
                  : "No X credentials - posts forced to dry-run"
              }
            >
              <CircleDot className="h-3 w-3" />X
            </Badge>
          </div>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
