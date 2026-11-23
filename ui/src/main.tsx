import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App, WorkspaceViewer } from "./App";
import { createApi, mockBackend } from "./api/client";
import "./styles.css";

// `?mock` in the page URL (or VITE_MOCK=1) answers requests in the browser from src/mocks.
const params = new URLSearchParams(location.search);
const mock = import.meta.env.VITE_MOCK === "1" || params.has("mock");
// `?browse` browses any store by document, as the viewer does on its own for a store too big to draw whole.
const browse = params.has("browse");
const mockApi = createApi(mockBackend);

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    {mock ? <App api={mockApi} mock browse={browse} /> : <WorkspaceViewer browse={browse} />}
  </StrictMode>,
);
