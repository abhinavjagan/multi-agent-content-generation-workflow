import * as React from "react";
import { cn } from "@/lib/utils";

export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md bg-gradient-to-r from-muted via-accent to-muted bg-[length:200%_100%] animate-shimmer",
        className,
      )}
      {...props}
    />
  );
}
