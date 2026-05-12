import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cn } from "@/lib/utils";

type Variant =
  | "default"
  | "secondary"
  | "outline"
  | "ghost"
  | "destructive"
  | "subtle"
  | "link";

type Size = "sm" | "md" | "lg" | "icon";

const VARIANT_CLASS: Record<Variant, string> = {
  // Warm orange -> coral gradient with a soft glow. Picks up
  // .btn-pop from index.css for the gradient + lift-on-hover; we
  // still set text-primary-foreground so disabled states (which drop
  // the gradient via the .btn-pop rule order) stay legible.
  default:
    "btn-pop text-primary-foreground",
  secondary:
    "bg-secondary text-secondary-foreground hover:bg-secondary/70 active:bg-secondary/90 border border-primary/15 shadow-sm",
  // Gradient-border outline that fills on hover. See .btn-pop-outline
  // for the mask trick. Keeps the page calm but pops on intent.
  outline: "btn-pop-outline",
  ghost:
    "text-foreground/85 hover:bg-primary/10 hover:text-primary active:bg-primary/15",
  destructive:
    "bg-destructive text-destructive-foreground hover:bg-destructive/90 shadow-[0_8px_24px_-8px_hsl(var(--destructive)/0.55)]",
  subtle:
    "bg-primary/10 text-primary hover:bg-primary/15 active:bg-primary/20",
  link: "text-primary underline-offset-4 hover:underline",
};

const SIZE_CLASS: Record<Size, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-9 px-4 text-sm gap-2",
  lg: "h-11 px-6 text-base gap-2",
  icon: "h-9 w-9 p-0",
};

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = "default",
      size = "md",
      loading,
      disabled,
      asChild,
      children,
      ...props
    },
    ref,
  ) => {
    const classes = cn(
      "inline-flex items-center justify-center rounded-md font-medium transition-colors",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      "disabled:pointer-events-none disabled:opacity-50",
      VARIANT_CLASS[variant],
      SIZE_CLASS[size],
      className,
    );

    // Radix `Slot` requires exactly ONE React element child. When `asChild`
    // is set we therefore pass `children` straight through (typically a
    // `<Link>` or `<a>` for navigation buttons) without wrapping it in a
    // fragment with a loading spinner -- those buttons don't have async
    // state in practice, and a fragment would still confuse Slot's prop
    // merging.
    if (asChild) {
      return (
        <Slot
          ref={ref}
          className={classes}
          aria-disabled={disabled || loading ? true : undefined}
          {...props}
        >
          {children}
        </Slot>
      );
    }

    return (
      <button
        ref={ref}
        className={classes}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? (
          <span className="inline-flex h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
        ) : null}
        {children}
      </button>
    );
  },
);
Button.displayName = "Button";
