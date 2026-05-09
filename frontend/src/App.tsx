import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "@/components/layout/AppShell";
import { ErrorBoundary } from "@/components/layout/ErrorBoundary";
import { Skeleton } from "@/components/ui/skeleton";

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Draft = lazy(() => import("@/pages/Draft"));
const PersonaList = lazy(() => import("@/pages/PersonaList"));
const PersonaCreate = lazy(() => import("@/pages/PersonaCreate"));
const PersonaDetail = lazy(() => import("@/pages/PersonaDetail"));
const PersonaRefine = lazy(() => import("@/pages/PersonaRefine"));
const PersonaEval = lazy(() => import("@/pages/PersonaEval"));
const Settings = lazy(() => import("@/pages/Settings"));
const NotFound = lazy(() => import("@/pages/NotFound"));

function PageLoading() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-10 w-1/3" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <TooltipProvider delayDuration={200}>
        <Suspense fallback={<PageLoading />}>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<Dashboard />} />
              <Route path="/draft" element={<Draft />} />
              <Route path="/personas" element={<PersonaList />} />
              <Route path="/personas/new" element={<PersonaCreate />} />
              <Route path="/personas/:id" element={<PersonaDetail />} />
              <Route path="/personas/:id/refine" element={<PersonaRefine />} />
              <Route path="/personas/:id/eval" element={<PersonaEval />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </Suspense>
      </TooltipProvider>
    </ErrorBoundary>
  );
}
