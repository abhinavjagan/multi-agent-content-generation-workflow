import { Outlet } from "react-router-dom";
import { AmbientBackground } from "./AmbientBackground";
import { TopNav } from "./TopNav";

export function AppShell() {
  return (
    <div className="relative flex min-h-full flex-col">
      <AmbientBackground />
      <TopNav />
      <main className="container flex-1 py-8">
        <Outlet />
      </main>
      <footer className="border-t border-border/40 py-4 text-center text-xs text-muted-foreground">
        local-only LangGraph + Ollama agent. never publishes anywhere. you copy
        the final draft yourself.
      </footer>
    </div>
  );
}
