import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { QuickWindow } from "./quick/QuickWindow";

const isQuickWindow = new URLSearchParams(window.location.search).get("window") === "quick";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {isQuickWindow ? <QuickWindow /> : <App />}
  </React.StrictMode>,
);
