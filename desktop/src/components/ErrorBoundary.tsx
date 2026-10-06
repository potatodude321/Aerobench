/** Keeps one failing page (or the 3D view) from blanking the whole application. */
import { Alert, Button, Code, Stack, Text } from "@mantine/core";
import { Component, ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
  title?: string;
  resetKey?: unknown;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("UI error:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Stack p="lg" maw={760}>
        <Alert color="red" title={this.props.title || "This page could not be displayed"}>
          <Text size="sm">
            The rest of the application keeps working, and running jobs are not affected. Try again; if the problem persists, the
            message below helps support.
          </Text>
          <Code block mt="xs">{String(this.state.error.message || this.state.error)}</Code>
          <Button mt="sm" size="xs" variant="light" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
        </Alert>
      </Stack>
    );
  }
}
