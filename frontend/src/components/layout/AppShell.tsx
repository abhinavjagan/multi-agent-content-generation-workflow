import { Outlet } from "react-router-dom";
import { TopNav } from "./TopNav";

export function AppShell() {
  return (
    <div className="flex min-h-full flex-col">
      <TopNav />
      <div className="grid-bg pointer-events-none absolute inset-x-0 top-0 -z-10 h-[40vh]" />
      <main className="container flex-1 py-6">
        <Outlet />
      </main>
      <footer className="border-t border-border/60 py-4 text-center text-xs text-muted-foreground">
        local-only LangGraph + Ollama agent. never expose this to the public internet.
      </footer>
    </div>
  );
}
