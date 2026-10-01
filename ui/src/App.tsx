import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { Shell } from "./components/Shell";
import { Empty, PageHeader } from "./components/ui";
import { Approvals } from "./pages/Approvals";
import { EvalDetail } from "./pages/EvalDetail";
import { Evals } from "./pages/Evals";
import { Overview } from "./pages/Overview";
import { RunDetail } from "./pages/RunDetail";
import { Tasks } from "./pages/Tasks";
import { Workflows } from "./pages/Workflows";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2_000, refetchOnWindowFocus: false, retry: 1 } },
});

function NotFound() {
  return (
    <div>
      <PageHeader title="Not found" />
      <Empty>there is no page here</Empty>
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Overview /> },
      { path: "runs/:runId", element: <RunDetail /> },
      { path: "approvals", element: <Approvals /> },
      { path: "tasks", element: <Tasks /> },
      { path: "evals", element: <Evals /> },
      { path: "evals/:evalId", element: <EvalDetail /> },
      { path: "workflows", element: <Workflows /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
