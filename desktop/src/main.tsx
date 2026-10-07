import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "./styles/app.css";

import { ActionIcon, Button, createTheme, MantineColorsTuple, MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

// Palettes built so shade 5 is the colour used for filled elements in the dark scheme.
const ice: MantineColorsTuple = ["#f0f6fc", "#dde9f6", "#c6daef", "#b0cbe8", "#9ec1e4", "#8db7e0", "#76a1cb", "#5f88b1", "#4b6e92", "#3a5671"];
const ok: MantineColorsTuple = ["#eaf6ef", "#d3ecdc", "#b8dfc7", "#9ccfb0", "#86c49f", "#74b98f", "#5ea179", "#4a8763", "#3a6c4f", "#2b523c"];
const err: MantineColorsTuple = ["#fcecec", "#f7d4d4", "#f0b8b8", "#ea9c9c", "#e58585", "#e07070", "#c75c5c", "#a94a4a", "#893b3b", "#6a2e2e"];
const warn: MantineColorsTuple = ["#fbf3e4", "#f5e3c2", "#eed09b", "#e6bd76", "#deae5c", "#d6a24a", "#bd8b39", "#9f732d", "#7f5b23", "#61451a"];
const graphite: MantineColorsTuple = ["#e3e6ea", "#c3c8cf", "#a1a9b4", "#6c7580", "#3a414b", "#2d333c", "#232830", "#1b1f25", "#15181d", "#111418"];

const theme = createTheme({
  primaryColor: "ice",
  primaryShade: { light: 6, dark: 5 },
  autoContrast: true,
  luminanceThreshold: 0.4,
  colors: {
    ice,
    dark: graphite,
    // status colours everywhere are the same three calm tones
    teal: ok,
    green: ok,
    lime: ok,
    red: err,
    yellow: warn,
    orange: warn,
    cyan: ice,
    blue: ice,
  },
  fontFamily: "Barlow, 'Segoe UI Variable Text', 'Segoe UI', system-ui, sans-serif",
  fontFamilyMonospace: "'JetBrains Mono', 'Cascadia Mono', Consolas, monospace",
  headings: { fontFamily: "'Barlow Semi Condensed', Barlow, 'Segoe UI', sans-serif", fontWeight: "600" },
  fontSizes: { xs: "12.5px", sm: "13.5px", md: "14.5px", lg: "16px", xl: "19px" },
  radius: { xs: "3px", sm: "4px", md: "6px", lg: "8px", xl: "12px" },
  defaultRadius: "sm",
  cursorType: "pointer",
  components: {
    Badge: {
      defaultProps: { variant: "light", radius: "sm" },
      styles: { root: { textTransform: "none", fontWeight: 500, letterSpacing: 0 } },
    },
    Tooltip: { defaultProps: { openDelay: 300, multiline: true, maw: 320, color: "dark.5" } },
    Paper: { defaultProps: { radius: "sm" } },
    Card: { defaultProps: { radius: "sm" } },
    // filled controls carry dark text on the light accent (and on the calm status colours)
    Button: Button.extend({
      styles: { root: { fontWeight: 500 } },
      vars: (_t, props) => ((props.variant ?? "filled") === "filled" ? { root: { "--button-color": "#10141a" } } : { root: {} }),
    }),
    ActionIcon: ActionIcon.extend({
      vars: (_t, props) => (props.variant === "filled" ? { root: { "--ai-color": "#10141a" } } : { root: {} }),
    }),
    SegmentedControl: { defaultProps: { radius: "sm", color: "dark.4" } },
    Tabs: { defaultProps: { color: "ice" } },
    Progress: { defaultProps: { radius: "xs", size: "sm" } },
    Notification: { defaultProps: { radius: "sm" } },
    Menu: { defaultProps: { radius: "sm" } },
    Select: { defaultProps: { comboboxProps: { radius: "sm" } } },
    Checkbox: { defaultProps: { iconColor: "dark.8" } },
    Radio: { defaultProps: { iconColor: "dark.8" } },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark" forceColorScheme="dark">
      <Notifications position="bottom-right" limit={4} />
      <App />
    </MantineProvider>
  </React.StrictMode>,
);
