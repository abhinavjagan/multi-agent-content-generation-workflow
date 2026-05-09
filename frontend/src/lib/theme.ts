/**
 * Tiny theme helper. Stores the user's preference in localStorage and
 * applies/removes the `dark` class on `<html>` to drive Tailwind's
 * `darkMode: "class"` strategy.
 */

import { create } from "zustand";

type Theme = "light" | "dark";

const KEY = "x-agent-theme";

function getInitial(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(KEY);
  if (stored === "light" || stored === "dark") return stored;
  if (window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
  return "light";
}

function applyTheme(theme: Theme): void {
  const html = document.documentElement;
  html.classList.toggle("dark", theme === "dark");
  html.style.colorScheme = theme;
}

interface ThemeStore {
  theme: Theme;
  toggle: () => void;
  set: (t: Theme) => void;
}

export const useTheme = create<ThemeStore>((set, get) => ({
  theme: getInitial(),
  toggle: () => {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    window.localStorage.setItem(KEY, next);
    applyTheme(next);
    set({ theme: next });
  },
  set: (t) => {
    window.localStorage.setItem(KEY, t);
    applyTheme(t);
    set({ theme: t });
  },
}));

export function bootstrapTheme(): void {
  applyTheme(getInitial());
}
