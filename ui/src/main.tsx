import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initPwa } from "./lib/pwa";
import { initTouchDetect } from "./lib/touch-detect";
import "./styles/globals.css";

initTouchDetect();

const root = document.getElementById("root");
if (!root) throw new Error("#root not found");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

initPwa(() => {
  // Layout will pick this up via usePwaUpdate; no-op default fine.
});
