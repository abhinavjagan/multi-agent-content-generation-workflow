import * as React from "react";
import { cn } from "@/lib/utils";

type Variant =
  | "default"
  | "secondary"
  | "outline"
  | "success"
  | "warning"
  | "destructive"
  | "muted";

const VARIANT_CLASS: Record<Variant, string> = {
  default: "bg-primary/15 text-primary border-primary/25",
  secondary: "bg-secondary text-secondary-foreground border-border",
  outline: "border-border text-foreground/80",
  success: "bg-success/15 text-success border-success/30",
  warning: "bg-warning/15 text-warning border-warning/35",
  destructive: "bg-destructive/15 text-destructive border-destructive/35",
  muted: "bg-muted text-muted-foreground border-border",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: Variant;
}

export function Badge({
  className,
  variant = "default",
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium",
        VARIANT_CLASS[variant],
        className,
      )}
      {...props}
    />
  );
}
