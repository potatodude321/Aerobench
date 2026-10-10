/**
 * A one-line guide above each page of the demo project (tag "demo"): what is already set up on
 * this step and what to do next, with a button to the next step.
 */
import { ActionIcon, Button, Text } from "@mantine/core";
import { IconArrowRight, IconX } from "@tabler/icons-react";
import type { Section } from "../state/store";

const STEPS: { id: Section; label: string; text: string }[] = [
  { id: "geometry", label: "Geometry", text: "The Ahmed body is already imported: the body and its four stilts, nose upstream. Nothing to do here." },
  { id: "tunnel", label: "Tunnel", text: "A large tunnel over a stationary floor, as in the original experiment, so no blockage correction is needed." },
  { id: "mesh", label: "Mesh", text: "A coarse mesh (about 0.3 million cells) with two extra refinement regions of the demo, “Rear slant and base” and “Wake behind the slant”. Generate it here to inspect the cells, or go straight to Run, which meshes first." },
  { id: "physics", label: "Physics", text: "40 m/s with the k-ω SST model. The run stops by itself once all convergence criteria are met (800 iterations at most)." },
  { id: "run", label: "Run", text: "Start the run and follow residuals, forces and the time left. Measured Cd is about 0.29; steady RANS on this coarse mesh usually gives 0.33 to 0.37." },
  { id: "results", label: "Results", text: "Pressure on the body and the wake behind it: the low pressure on the slant and the rear face is where most of the drag comes from." },
  { id: "optimise", label: "Optimise", text: "Shape zones over the rear slant and the rear taper are ready (Shape zones tab). Try a change, then “Optimise these”; start with 6 to 8 designs, each is a full run." },
];

export function DemoGuide({ section, onGo, onClose }: { section: Section; onGo: (s: Section) => void; onClose: () => void }) {
  const i = STEPS.findIndex((s) => s.id === section);
  const step = STEPS[i];
  const next = i >= 0 ? STEPS[i + 1] : STEPS[0];
  return (
    <div className="ab-demo-guide" role="note">
      <Text size="xs" fw={600} c="var(--ab-accent)" style={{ whiteSpace: "nowrap" }}>
        Demo{step ? ` · ${i + 1} of ${STEPS.length}` : ""}
      </Text>
      <Text size="xs" style={{ flex: 1, minWidth: 0 }}>
        {step ? step.text : "Follow the workflow steps on the left; the guide picks up on each of them."}
      </Text>
      {next && (
        <Button size="compact-xs" variant="subtle" rightSection={<IconArrowRight size={13} />} onClick={() => onGo(next.id)}>
          {next.label}
        </Button>
      )}
      <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClose} aria-label="Hide the demo guide">
        <IconX size={13} />
      </ActionIcon>
    </div>
  );
}
