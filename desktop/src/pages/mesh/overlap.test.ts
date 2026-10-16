import { describe, expect, it } from "vitest";
import { overlapNotes } from "./RegionEditor";

const box = (id: string, min: number[], max: number[], level: number, auto = false) => ({
  id, name: id, kind: auto ? "vehicle_box" : "user", level, auto, enabled: true, shape: { kind: "box", min, max },
});

describe("refinement box overlaps", () => {
  it("names a box that lies inside a finer one, and only the second of two identical boxes", () => {
    const n = overlapNotes([
      box("outer", [0, 0, 0], [4, 2, 2], 4, true),
      box("inner", [1, 0, 0], [2, 1, 1], 3),
      box("same_a", [5, 0, 0], [6, 1, 1], 3),
      box("same_b", [5, 0, 0], [6, 1, 1], 3),
    ]);
    expect(n.inner).toMatch(/^No effect: it lies inside “outer”/);
    expect(n.same_a).toBeUndefined();
    expect(n.same_b).toMatch(/inside “same_a”/);
  });
  it("tells your own box where a finer one takes over, but not the nested automatic ones", () => {
    const n = overlapNotes([
      box("wake", [0, 0, 0], [4, 2, 2], 3, true),
      box("near", [1, 0, 0], [2, 1, 1], 5, true),
      box("mine", [1.5, 0, 0], [6, 1, 1], 4),
    ]);
    expect(n.wake).toBeUndefined();
    expect(n.mine).toMatch(/“near”.*finer level there applies/);
  });
});
