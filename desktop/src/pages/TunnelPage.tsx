import { Button, SegmentedControl, Select, SimpleGrid, Slider, Switch, Text } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { api } from "../api/client";
import type { MeshPlan } from "../api/types";
import { fmt } from "../components/ColorLegend";
import { notifyError } from "../components/notify";
import { Hint, KeyValue, NumField, PageLayout, SectionTitle, ValidationList } from "../components/ui";
import { pid, useApp } from "../state/store";
import { decodeAbg } from "../viewport/abg";
import { Viewer } from "../viewport/Viewer";
import { Viewport3D } from "../viewport/Viewport3D";
import { drawPlan } from "./MeshPage";

export function usePlan(): [MeshPlan | null, any, boolean] {
  const { project } = useApp();
  const [plan, setPlan] = useState<MeshPlan | null>(null);
  const [extra, setExtra] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const h = project?.staleness.hashes.mesh;
  useEffect(() => {
    if (!project?.config.geometry.revision_id) return;
    let cancelled = false;
    setLoading(true);
    api
      .get(`/api/p/${pid()}/mesh/plan`)
      .then((r) => {
        if (!cancelled) {
          setPlan(r.plan);
          setExtra(r);
        }
      })
      .catch((e) => !cancelled && notifyError(e))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [h, project?.config.geometry.revision_id, project?.staleness.hashes.solve]);
  return [plan, extra, loading];
}

/** Height over the ground and angle of an aero element studied alone. */
function ElementPosition() {
  const { project, refreshProject, bumpGeometry } = useApp();
  const e = project!.config.element!;
  const [busy, setBusy] = useState(false);
  const apply = async (patch: Record<string, unknown>) => {
    setBusy(true);
    try {
      await api.patch(`/api/p/${pid()}/element`, patch);
      await refreshProject();
      bumpGeometry();
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(false);
    }
  };
  const ground = e.environment === "ground_effect";
  return (
    <>
      <SectionTitle first>Position of the element</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={e.environment} disabled={busy} onChange={(v) => apply({ environment: v })}
        data={[{ value: "free_stream", label: "Free stream" }, { value: "ground_effect", label: "Over the ground" }]} />
      <SimpleGrid cols={2} spacing="xs" mt={10}>
        {ground ? (
          <NumField label="Lowest point above the ground" units="mm" value={e.ground_clearance * 1000} decimals={1} min={1}
            onCommit={(v) => v > 0 && apply({ ground_clearance: v / 1000 })} />
        ) : (
          <NumField label="Height above the ground" units="chords" value={e.free_stream_chords} decimals={1} min={2} max={20}
            onCommit={(v) => apply({ free_stream_chords: Math.max(2, Math.min(20, v)) })} />
        )}
        <NumField label="Angle, trailing edge up" units="°" value={e.angle_deg} decimals={2} min={-45} max={45}
          onCommit={(v) => apply({ angle_deg: Math.max(-45, Math.min(45, v)) })} />
      </SimpleGrid>
      <Hint style={{ marginTop: 6 }}>
        {ground
          ? "The element sits over a road moving with the air, as a front wing or splitter does on the car."
          : "Far enough from the ground that it has no measurable effect, as for a rear wing or a flap studied on its own."}{" "}
        The angle turns the element about its span; the clearance is kept.
      </Hint>
    </>
  );
}

export function TunnelPage() {
  const { project, patchConfig, geometryVersion, setSection } = useApp();
  const cfg = project!.config;
  const t = cfg.tunnel;
  const op = cfg.operating;
  const isElement = cfg.application === "aero_element" && !!cfg.element;
  const [plan] = usePlan();
  const viewer = useRef<Viewer | null>(null);
  const [geomLoaded, setGeomLoaded] = useState(0);

  useEffect(() => {
    const v = viewer.current;
    if (!v || !cfg.geometry.revision_id) return;
    api.binary(`/api/p/${pid()}/geometry/preview.abg`).then((b) => {
      v.setGeometry(decodeAbg(b));
      setGeomLoaded((x) => x + 1);
    }).catch(notifyError);
  }, [cfg.geometry.revision_id, geometryVersion]);

  useEffect(() => {
    const v = viewer.current;
    if (!v || !plan) return;
    drawPlan(v, plan, { regions: false, domain: true });
    const [x0, y0, z0] = plan.domain_min;
    const [x1, y1, z1] = plan.domain_max;
    v.view("iso", new THREE.Box3(new THREE.Vector3(x0, y0, z0), new THREE.Vector3(x1, y1, z1)));
  }, [plan, geomLoaded]);

  const setPreset = async (p: string) => {
    try {
      await api.post(`/api/p/${pid()}/presets/tunnel/${p}`);
      await useApp.getState().refreshProject();
    } catch (e) {
      notifyError(e);
    }
  };

  const yawRange = t.design_yaw_range_deg;
  const L = isElement ? "chords" : "L";
  const side = (
    <>
      {isElement && <ElementPosition />}
      <SectionTitle first={!isElement}>Size of the tunnel</SectionTitle>
      <SegmentedControl
        size="xs"
        fullWidth
        value={t.preset}
        onChange={setPreset}
        data={[
          { value: "fast", label: "Fast" },
          { value: "balanced", label: "Balanced" },
          { value: "high_accuracy", label: "High accuracy" },
          { value: "custom", label: "Custom" },
        ]}
      />
      <SimpleGrid cols={2} spacing="xs" mt={10}>
        <NumField label={isElement ? "Ahead of the leading edge" : "Ahead of the nose"} units={L} value={t.upstream} decimals={2} onCommit={(v) => patchConfig({ tunnel: { upstream: v, preset: "custom" } })} />
        <NumField label={isElement ? "Behind the trailing edge" : "Behind the tail"} units={L} value={t.downstream} decimals={2} onCommit={(v) => patchConfig({ tunnel: { downstream: v, preset: "custom" } })} />
        <NumField label={isElement ? "Beside each tip" : "Beside each side"} units={L} value={t.lateral} decimals={2} onCommit={(v) => patchConfig({ tunnel: { lateral: v, preset: "custom" } })} />
        <NumField label={isElement ? "Above the element" : "Above the roof"} units={L} value={t.top} decimals={2} onCommit={(v) => patchConfig({ tunnel: { top: v, preset: "custom" } })} />
      </SimpleGrid>
      <Hint style={{ marginTop: 6 }}>
        {isElement ? "In chords (the element's length along the flow)." : "In vehicle lengths L."} A larger tunnel disturbs the flow less but costs cells; the blockage ratio shows the consequence.
      </Hint>
      {plan && (
        <div style={{ marginTop: 12 }}>
          <KeyValue
            rows={[
              ["Tunnel size", `${fmt(plan.domain_max[0] - plan.domain_min[0], 2)} × ${fmt(plan.domain_max[1] - plan.domain_min[1], 2)} × ${fmt(plan.domain_max[2] - plan.domain_min[2], 2)} m`],
              ["Blockage ratio", plan.blockage_ratio !== null ? `${(plan.blockage_ratio * 100).toFixed(2)} %` : "—"],
              ["Background cells", `${plan.n_cells_block.join(" × ")}, ${fmt(plan.base_cell, 3)} m`],
              ["Ground plane at", `z = ${fmt(plan.ground_z, 4)} m`],
            ]}
          />
          {plan.warnings.map((w, i) => (
            <Hint key={i} style={{ color: "var(--ab-warn)", marginTop: 6 }}>
              {w}
            </Hint>
          ))}
        </div>
      )}

      {!isElement && <>
      <SectionTitle>Ground</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={op.ground} onChange={(v) => patchConfig({ operating: { ground: v } })}
        data={[{ value: "moving", label: "Moving (rolling road)" }, { value: "stationary", label: "Stationary" }]} />
      {op.ground === "moving" && (
        <Select size="xs" mt={10} label="When the car is yawed, the ground moves" value={op.yaw_ground_mode} onChange={(v) => v && patchConfig({ operating: { yaw_ground_mode: v } })}
          data={[{ value: "road", label: "Along the car (road), at U∞·cos β" }, { value: "tunnel_belt", label: "With the air (tunnel belt)" }]} />
      )}
      <Text size="xs" c="dimmed" fw={500} mt={12} mb={2}>
        Tyre contact patch
      </Text>
      <Slider size="sm" min={0} max={0.04} step={0.0025} value={t.contact_patch_fraction} label={(v) => `${(v * 100).toFixed(2)} % of the radius`}
        onChangeEnd={(v) => patchConfig({ tunnel: { contact_patch_fraction: v } })} marks={[{ value: 0.01, label: "1 %" }, { value: 0.02, label: "2 %" }, { value: 0.03, label: "3 %" }]} mb={22} />
      <Hint>The floor is raised slightly into the tyres so they stand on a small patch instead of touching it along a line, which meshes badly.</Hint>
      </>}

      <SectionTitle>Symmetry</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={t.symmetry} onChange={(v) => patchConfig({ tunnel: { symmetry: v } })}
        data={[{ value: "full", label: isElement ? "Whole element" : "Whole vehicle" }, { value: "half", label: "Half (symmetry plane)" }]} />
      <ValidationList issues={project!.validation} filter={(i) => i.path.startsWith("tunnel")} />

      <SectionTitle>Outer boundaries and yaw</SectionTitle>
      <Select size="xs" label="Conditions on the tunnel walls" value={t.farfield_bc} onChange={(v) => v && patchConfig({ tunnel: { farfield_bc: v } })}
        data={[
          { value: "auto", label: "Automatic (inlet and outlet straight ahead, free stream when yawed)" },
          { value: "inlet_outlet_slip", label: "Velocity inlet, pressure outlet, slip walls" },
          { value: "freestream", label: "Free stream on every outer face" },
        ]} />
      <Switch size="xs" mt={12} label="Prepare one mesh for a range of yaw angles"
        checked={!!yawRange} onChange={(e) => patchConfig({ tunnel: { design_yaw_range_deg: e.currentTarget.checked ? [-10, 10] : null } })} />
      {yawRange && (
        <SimpleGrid cols={2} spacing="xs" mt={8}>
          <NumField label="Yaw from" units="°" value={yawRange[0]} decimals={1} onCommit={(v) => patchConfig({ tunnel: { design_yaw_range_deg: [v, yawRange[1]] } })} />
          <NumField label="Yaw to" units="°" value={yawRange[1]} decimals={1} onCommit={(v) => patchConfig({ tunnel: { design_yaw_range_deg: [yawRange[0], v] } })} />
        </SimpleGrid>
      )}
    </>
  );

  return (
    <PageLayout
      side={side}
      title="Tunnel"
      lede={isElement ? "Where the element sits, and the virtual wind tunnel around it."
        : "The virtual wind tunnel around the vehicle: its size, the road surface and how the outer boundaries behave."}
      footer={
        <Button fullWidth variant="default" rightSection={<IconArrowRight size={15} />} onClick={() => setSection("mesh")}>
          Continue to the mesh
        </Button>
      }
    >
      <Viewport3D onReady={(v) => (viewer.current = v)} />
    </PageLayout>
  );
}
