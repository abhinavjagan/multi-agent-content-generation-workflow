import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";

import App from "./App";
import { ApiError } from "./lib/api";
import { bootstrapTheme } from "./lib/theme";
import "./index.css";

bootstrapTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 2;
      },
      staleTime: 5_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
      <Toaster
        richColors
        position="top-right"
        theme="system"
        closeButton
        toastOptions={{
          classNames: {
            toast:
              "rounded-md border border-border bg-popover text-popover-foreground shadow-lg",
          },
        }}
      />
    </QueryClientProvider>
  </React.StrictMode>,
);
