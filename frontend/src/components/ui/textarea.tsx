import * as React from "react";
import { cn } from "@/lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  autoSize?: boolean;
};

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, autoSize, onInput, rows = 4, ...props }, ref) => {
    const innerRef = React.useRef<HTMLTextAreaElement | null>(null);
    React.useImperativeHandle(
      ref,
      () => innerRef.current as HTMLTextAreaElement,
    );

    const handleInput = React.useCallback(
      (event: React.FormEvent<HTMLTextAreaElement>) => {
        if (autoSize && innerRef.current) {
          innerRef.current.style.height = "auto";
          innerRef.current.style.height = `${innerRef.current.scrollHeight}px`;
        }
        onInput?.(event);
      },
      [autoSize, onInput],
    );

    React.useEffect(() => {
      if (autoSize && innerRef.current) {
        innerRef.current.style.height = "auto";
        innerRef.current.style.height = `${innerRef.current.scrollHeight}px`;
      }
    }, [autoSize, props.value]);

    return (
      <textarea
        ref={innerRef}
        rows={rows}
        onInput={handleInput}
        className={cn(
          "flex min-h-[5rem] w-full rounded-md border border-input bg-background/50 px-3 py-2 text-sm",
          "placeholder:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "transition-colors resize-y scrollbar-thin",
          className,
        )}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";
