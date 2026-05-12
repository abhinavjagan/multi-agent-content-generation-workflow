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
  default: "bg-primary/20 text-primary border-primary/40 shadow-[0_2px_8px_-4px_hsl(var(--primary)/0.45)]",
  secondary: "bg-secondary text-secondary-foreground border-primary/15",
  outline: "border-primary/30 text-foreground/85",
  success: "bg-success/20 text-success border-success/45 shadow-[0_2px_8px_-4px_hsl(var(--success)/0.4)]",
  warning: "bg-warning/20 text-warning border-warning/45 shadow-[0_2px_8px_-4px_hsl(var(--warning)/0.4)]",
  destructive: "bg-destructive/20 text-destructive border-destructive/45 shadow-[0_2px_8px_-4px_hsl(var(--destructive)/0.4)]",
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
