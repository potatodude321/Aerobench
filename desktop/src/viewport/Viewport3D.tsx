import { ActionIcon, Group, Paper, Tooltip } from "@mantine/core";
import {
  IconArrowBigLeftLines,
  IconArrowBigRightLines,
  IconBox,
  IconCamera,
  IconEye,
  IconLayersSubtract,
  IconView360,
} from "@tabler/icons-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { CameraState, Viewer } from "./Viewer";

export interface Viewport3DHandle {
  viewer: Viewer | null;
}

interface Props {
  onReady?: (v: Viewer) => void;
  onCamera?: (c: CameraState) => void;
  toolbar?: boolean;
  children?: React.ReactNode;
  height?: number | string;
}

export const Viewport3D = forwardRef<Viewport3DHandle, Props>(function Viewport3D({ onReady, onCamera, toolbar = true, children, height }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const [viewer, setViewer] = useState<Viewer | null>(null);
  useImperativeHandle(ref, () => ({ viewer }), [viewer]);

  const [glError, setGlError] = useState<string | null>(null);

  useEffect(() => {
    if (!host.current) return;
    let v: Viewer;
    try {
      v = new Viewer(host.current);
    } catch (e) {
      // No WebGL (remote desktop, virtual machine, blocked or very old GPU driver): everything
      // except the in-app 3D view keeps working.
      setGlError(String((e as Error)?.message || e));
      return;
    }
    setViewer(v);
    onReady?.(v);
    return () => v.dispose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (viewer) viewer.onCameraChange = onCamera;
  }, [viewer, onCamera]);

  const saveImage = () => {
    if (!viewer) return;
    const a = document.createElement("a");
    a.href = viewer.snapshot();
    a.download = "view.png";
    a.click();
  };

  return (
    <div style={{ position: "relative", width: "100%", height: height ?? "100%", minHeight: 200, overflow: "hidden", borderRadius: 8 }}>
      <div ref={host} style={{ position: "absolute", inset: 0 }} data-testid="viewport" />
      {glError && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div style={{ maxWidth: 460, color: "#c1c7d0", fontSize: 13, lineHeight: 1.5, textAlign: "center" }}>
            <b>3D view unavailable.</b> The graphics system did not provide WebGL (common over remote desktop, in virtual machines or with
            outdated GPU drivers). All other functions work; flow images can still be produced by ParaView in reports or with “Open in
            ParaView”.
            <div style={{ opacity: 0.6, fontSize: 11, marginTop: 8 }}>{glError}</div>
          </div>
        </div>
      )}
      {toolbar && viewer && (
        <Paper shadow="sm" p={4} radius="md" style={{ position: "absolute", top: 8, right: 8, background: "rgba(20,23,29,0.85)" }}>
          <Group gap={2}>
            <Tooltip label="Isometric (front)">
              <ActionIcon variant="subtle" onClick={() => viewer.view("iso")}>
                <IconView360 size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Isometric (rear)">
              <ActionIcon variant="subtle" onClick={() => viewer.view("iso_rear")}>
                <IconLayersSubtract size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Front">
              <ActionIcon variant="subtle" onClick={() => viewer.view("front")}>
                <IconArrowBigRightLines size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Rear">
              <ActionIcon variant="subtle" onClick={() => viewer.view("rear")}>
                <IconArrowBigLeftLines size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Side">
              <ActionIcon variant="subtle" onClick={() => viewer.view("side")}>
                <IconBox size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Top">
              <ActionIcon variant="subtle" onClick={() => viewer.view("top")}>
                <IconEye size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Save image">
              <ActionIcon variant="subtle" onClick={saveImage}>
                <IconCamera size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Paper>
      )}
      {children}
    </div>
  );
});
