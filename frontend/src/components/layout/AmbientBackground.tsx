/**
 * Full-viewport fixed ambient background.
 *
 * Combines a grainy warm-violet -> soft-cyan gradient (see ``.grid-bg``
 * and ``.bg-grainy`` in index.css, themselves inspired by
 * liquid-glass-react and grainy-gradients) with two heavily-blurred
 * coolshape accents. Everything is ``aria-hidden`` and
 * ``pointer-events-none`` so it never interferes with the foreground.
 */

import { CoolShape } from "@/components/decor/CoolShape";

export function AmbientBackground() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
    >
      <div className="grid-bg bg-grainy absolute inset-0" />

      <div
        className="coolshape-blur absolute"
        style={{ top: "-6rem", left: "-5rem", opacity: 0.65 }}
      >
        <CoolShape
          kind="blob"
          fromColor="hsl(248 92% 65%)"
          toColor="hsl(322 88% 62%)"
          size={480}
        />
      </div>

      <div
        className="coolshape-blur absolute"
        style={{ top: "18%", right: "-7rem", opacity: 0.6 }}
      >
        <CoolShape
          kind="ring"
          fromColor="hsl(195 95% 55%)"
          toColor="hsl(248 92% 65%)"
          size={420}
        />
      </div>

      <div
        className="coolshape-blur absolute"
        style={{ bottom: "-9rem", left: "32%", opacity: 0.55 }}
      >
        <CoolShape
          kind="polygon"
          fromColor="hsl(322 88% 62%)"
          toColor="hsl(195 95% 55%)"
          size={520}
        />
      </div>

      <div
        className="coolshape-blur absolute"
        style={{ top: "60%", left: "8%", opacity: 0.4 }}
      >
        <CoolShape
          kind="star"
          fromColor="hsl(248 92% 65%)"
          toColor="hsl(322 88% 62%)"
          size={300}
        />
      </div>
    </div>
  );
}

export default AmbientBackground;
