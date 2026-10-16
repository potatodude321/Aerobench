/** "Mirror a copy" for a box: across a plane normal to x, y or z at a position you choose. */
import { ActionIcon, Button, Menu, NumberInput, Select, Stack, Text, Tooltip } from "@mantine/core";
import { IconFlipHorizontal } from "@tabler/icons-react";
import { useState } from "react";

const AX = ["x", "y", "z"] as const;

export function MirrorMenu({ defaults, onMirror, label = "Mirror a copy" }: {
  /** plane position offered first, per axis (x, y, z) */
  defaults: number[];
  onMirror: (axis: number, plane: number) => void;
  label?: string;
}) {
  const [axis, setAxis] = useState("1");
  const a = Number(axis);
  const [plane, setPlane] = useState<number | "">("");
  const p = plane === "" ? defaults[a] : plane;
  return (
    <Menu position="bottom-end" width={240} closeOnItemClick={false}>
      <Menu.Target>
        <Tooltip label={label}>
          <ActionIcon size="sm" variant="subtle" color="gray" aria-label={label}>
            <IconFlipHorizontal size={14} />
          </ActionIcon>
        </Tooltip>
      </Menu.Target>
      <Menu.Dropdown p={8}>
        <Stack gap={6}>
          <Select size="xs" label="Mirror across" value={axis} allowDeselect={false} onChange={(v) => { if (v) { setAxis(v); setPlane(""); } }}
            data={[{ value: "1", label: "y (left and right)" }, { value: "0", label: "x (front and rear)" }, { value: "2", label: "z (up and down)" }]} />
          <NumberInput size="xs" label={`Plane at ${AX[a]} =`} value={Number(p.toFixed(4))} decimalScale={4} step={0.01}
            onChange={(v) => setPlane(v === "" ? "" : Number(v))} rightSection={<Text size="xs" c="dimmed" pr={8}>m</Text>} />
          <Button size="xs" onClick={() => onMirror(a, Number(p))}>Add mirrored copy</Button>
        </Stack>
      </Menu.Dropdown>
    </Menu>
  );
}
