import { notifications } from "@mantine/notifications";
import { EngineError } from "../api/client";

export function notifyError(e: unknown, title?: string): void {
  if (e instanceof EngineError) {
    const d = e.diagnostic;
    notifications.show({
      color: "red",
      title: title || d.summary,
      message: [d.likely_cause, d.action].filter(Boolean).join(" — ") || d.code,
      autoClose: 12000,
    });
    // Keep the raw details available in the console/log panel
    if (d.details) console.warn(`[${d.code}]`, d.details);
    return;
  }
  notifications.show({ color: "red", title: title || "Error", message: String((e as Error)?.message ?? e), autoClose: 10000 });
}

export function notifyOk(message: string, title?: string): void {
  notifications.show({ color: "teal", title, message, autoClose: 4000 });
}

export function notifyInfo(message: string, title?: string): void {
  notifications.show({ color: "blue", title, message, autoClose: 6000 });
}
