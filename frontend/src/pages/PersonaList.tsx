import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ChartLine,
  Edit3,
  Plus,
  Trash2,
  Users,
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
import { EmptyState } from "@/components/ui/empty";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, deletePersona, listPersonas } from "@/lib/api";
import { formatRelative } from "@/lib/utils";
import type { PersonaSummary } from "@/lib/types";

export default function PersonaList() {
  const personas = useQuery({ queryKey: ["personas"], queryFn: listPersonas });
  const [pendingDelete, setPendingDelete] = useState<PersonaSummary | null>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deletePersona(id),
    onSuccess: (_data, id) => {
      toast.success(`Deleted ${id}`);
      qc.invalidateQueries({ queryKey: ["personas"] });
      qc.invalidateQueries({ queryKey: ["health"] });
      setPendingDelete(null);
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.detail : (err as Error).message, {
        description:
          "Persona may already be gone — refresh the list to check, or remove the directory under ~/.x-agent/personas manually if it's stuck.",
      });
    },
  });

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Personas</h1>
          <p className="text-sm text-muted-foreground">
            Saved voices the agent can write as. Each persona has a spec, a raw
            interview transcript, and embedded examples.
          </p>
        </div>
        <Button asChild>
          <Link to="/personas/new">
            <Plus className="h-4 w-4" />
            New persona
          </Link>
        </Button>
      </div>

      <Card>
        <CardHeader className="border-b border-border pb-4">
          <CardTitle className="text-base">All personas</CardTitle>
          <CardDescription>
            {personas.data ? `${personas.data.length} saved` : "loading…"}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {personas.isLoading ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : !personas.data?.length ? (
            <div className="p-6">
              <EmptyState
                icon={<Users className="h-5 w-5" />}
                title="No personas yet"
                description="Run an interactive interview to capture someone's voice. Drafts can then be conditioned on the resulting persona."
                action={
                  <Button asChild>
                    <Link to="/personas/new">
                      <Plus className="h-4 w-4" />
                      Create persona
                    </Link>
                  </Button>
                }
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-card text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium">Name</th>
                    <th className="px-4 py-3 text-left font-medium">ID</th>
                    <th className="px-4 py-3 text-left font-medium">Type</th>
                    <th className="px-4 py-3 text-left font-medium">Voice</th>
                    <th className="px-4 py-3 text-left font-medium">Updated</th>
                    <th className="px-4 py-3 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {personas.data.map((p) => (
                    <tr
                      key={p.id}
                      className="border-t border-border transition-colors hover:bg-secondary/40"
                    >
                      <td className="px-4 py-3">
                        <Link
                          to={`/personas/${p.id}`}
                          className="font-medium hover:underline"
                        >
                          {p.name}
                        </Link>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                        {p.id}
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={p.is_real_person ? "default" : "secondary"}>
                          {p.is_real_person ? "real" : "fictional"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        f={p.voice_formality} · {p.voice_brevity} · {p.voice_humor}
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {formatRelative(p.updated_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => navigate(`/draft?persona=${p.id}`)}
                            title="Draft as this persona"
                          >
                            <ArrowRight className="h-3.5 w-3.5" />
                            Draft
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              navigate(`/personas/${p.id}/refine`)
                            }
                            title="Refine"
                          >
                            <Edit3 className="h-3.5 w-3.5" />
                            Refine
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => navigate(`/personas/${p.id}/eval`)}
                            title="Evaluate"
                          >
                            <ChartLine className="h-3.5 w-3.5" />
                            Eval
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setPendingDelete(p)}
                            title="Delete"
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={!!pendingDelete}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this persona?</DialogTitle>
            <DialogDescription>
              This permanently removes the spec, transcript, and embeddings
              for{" "}
              <span className="font-medium text-foreground">
                {pendingDelete?.name}
              </span>{" "}
              <span className="font-mono text-xs">
                ({pendingDelete?.id})
              </span>
              . This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              loading={deleteMutation.isPending}
              onClick={() =>
                pendingDelete && deleteMutation.mutate(pendingDelete.id)
              }
            >
              <Trash2 className="h-4 w-4" />
              Delete persona
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
