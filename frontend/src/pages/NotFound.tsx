import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[50vh] max-w-md flex-col items-center justify-center gap-3 text-center">
      <h1 className="font-mono text-7xl font-bold tracking-tighter text-primary/80">
        404
      </h1>
      <p className="text-muted-foreground">
        That page doesn't exist. Did the URL get truncated?
      </p>
      <Button asChild>
        <Link to="/">Back to dashboard</Link>
      </Button>
    </div>
  );
}
