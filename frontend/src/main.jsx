import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
    },
  },
});

async function bootstrap() {
  let mockApi = null;
  if (import.meta.env.VITE_RENDER_NODE_MOCK === "true") {
    ({ mockApi } = await import("./mockApi"));
  }

  createRoot(document.getElementById("root")).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App mockApi={mockApi} />
      </QueryClientProvider>
    </StrictMode>,
  );
}

bootstrap();
