/**
 * Inline SVG "coolshape" accents inspired by coolshapes-vue
 * (https://github.com/xiaoluoboding/coolshapes-vue).
 *
 * These are decorative only: non-interactive, ``aria-hidden``, low
 * opacity, and intended to sit behind text content with a heavy blur
 * (see ``.coolshape-blur`` and ``AmbientBackground``). Keeping them as
 * inline React components avoids the npm dependency and gives us full
 * control over the conic-gradient fills via SVG ``<defs>``.
 */

import { type CSSProperties } from "react";

export type CoolShapeKind =
  | "blob"
  | "star"
  | "pill"
  | "polygon"
  | "asterisk"
  | "ring";

export interface CoolShapeProps {
  kind?: CoolShapeKind;
  /** Two-stop vibrant gradient. Defaults to violet -> cyan. */
  fromColor?: string;
  toColor?: string;
  size?: number | string;
  className?: string;
  style?: CSSProperties;
}

const PATHS: Record<CoolShapeKind, string> = {
  blob:
    "M73 12c14 7 31 5 39 17s-4 27-3 41-14 23-30 27-31-7-46-14S6 68 6 53s10-28 24-37 28-12 43-4z",
  star:
    "M50 4l11 28 30 4-22 22 6 30-25-14-25 14 6-30L9 36l30-4z",
  pill: "M20 30 H80 A22 22 0 0 1 80 74 H20 A22 22 0 0 1 20 30 Z",
  polygon: "M50 4 L92 32 L80 80 L20 80 L8 32 Z",
  asterisk:
    "M48 6h4l3 26 22-14 2 4-22 14 26 3v4l-26 3 22 14-2 4-22-14-3 26h-4l-3-26-22 14-2-4 22-14L18 53v-4l26-3L22 32l2-4 22 14z",
  ring:
    "M50 8a42 42 0 1 0 0 84 42 42 0 1 0 0-84zm0 18a24 24 0 1 1 0 48 24 24 0 1 1 0-48z",
};

export function CoolShape({
  kind = "blob",
  fromColor = "hsl(248 90% 65%)",
  toColor = "hsl(322 85% 62%)",
  size = 220,
  className,
  style,
}: CoolShapeProps) {
  const gid = `coolshape-${kind}-${Math.random().toString(36).slice(2, 8)}`;
  return (
    <svg
      aria-hidden
      focusable={false}
      viewBox="0 0 100 100"
      width={size}
      height={size}
      className={className}
      style={style}
      role="presentation"
    >
      <defs>
        <linearGradient id={gid} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={fromColor} />
          <stop offset="100%" stopColor={toColor} />
        </linearGradient>
      </defs>
      <path d={PATHS[kind]} fill={`url(#${gid})`} />
    </svg>
  );
}

export default CoolShape;
