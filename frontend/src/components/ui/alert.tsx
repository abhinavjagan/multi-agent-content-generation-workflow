import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "destructive" | "warning" | "success";

const VARIANT_CLASS: Record<Variant, string> = {
  default: "border-border bg-card text-card-foreground",
  destructive:
    "border-destructive/40 bg-destructive/10 text-destructive [&>svg]:text-destructive",
  warning:
    "border-warning/40 bg-warning/10 text-warning-foreground [&>svg]:text-warning",
  success:
    "border-success/40 bg-success/10 text-success-foreground [&>svg]:text-success",
};

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: Variant;
}

export function Alert({ className, variant = "default", ...props }: AlertProps) {
  return (
    <div
      role="alert"
      className={cn(
        "relative flex w-full gap-3 rounded-md border p-4 text-sm [&>svg]:mt-0.5 [&>svg]:h-4 [&>svg]:w-4",
        VARIANT_CLASS[variant],
        className,
      )}
      {...props}
    />
  );
}

export function AlertTitle({
  className,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h5
      className={cn("font-semibold leading-none tracking-tight", className)}
      {...props}
    />
  );
}

export function AlertDescription({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("text-sm leading-relaxed", className)} {...props} />;
}
