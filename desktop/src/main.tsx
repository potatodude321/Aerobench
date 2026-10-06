import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "./styles/app.css";

import { createTheme, MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

const theme = createTheme({
  primaryColor: "cyan",
  defaultRadius: "md",
  fontFamily: "Inter, 'Segoe UI', system-ui, -apple-system, sans-serif",
  fontFamilyMonospace: "'JetBrains Mono', 'Cascadia Code', Consolas, monospace",
  headings: { fontWeight: "650" },
  colors: {
    dark: ["#c9ced6", "#a6adb8", "#7c8592", "#596270", "#3a414c", "#2a3039", "#1d2129", "#161a20", "#111419", "#0c0e12"],
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Notifications position="bottom-right" limit={5} />
      <App />
    </MantineProvider>
  </React.StrictMode>,
);
