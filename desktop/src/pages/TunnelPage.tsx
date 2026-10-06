import { Alert, Paper, SegmentedControl, Select, SimpleGrid, Slider, Stack, Switch, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { api } from "../api/client";
import type { MeshPlan } from "../api/types";
import { fmt } from "../components/ColorLegend";
import { notifyError } from "../components/notify";
import { KeyValue, NumField, PageLayout, SectionTitle, ValidationList } from "../components/ui";
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

export function TunnelPage() {
  const { project, patchConfig, geometryVersion } = useApp();
  const cfg = project!.config;
  const t = cfg.tunnel;
  const op = cfg.operating;
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
  const side = (
    <Stack gap="xs">
      <SectionTitle>Domain preset</SectionTitle>
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
      <Text size="xs" c="dimmed">
        Extents in vehicle lengths L, measured from the vehicle's nose, tail, sides and roof. No single rule is mandatory — the blockage ratio below shows the consequence.
      </Text>
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Upstream" units="L" value={t.upstream} decimals={2} onCommit={(v) => patchConfig({ tunnel: { upstream: v, preset: "custom" } })} />
        <NumField label="Downstream" units="L" value={t.downstream} decimals={2} onCommit={(v) => patchConfig({ tunnel: { downstream: v, preset: "custom" } })} />
        <NumField label="Lateral (each side)" units="L" value={t.lateral} decimals={2} onCommit={(v) => patchConfig({ tunnel: { lateral: v, preset: "custom" } })} />
        <NumField label="Above roof" units="L" value={t.top} decimals={2} onCommit={(v) => patchConfig({ tunnel: { top: v, preset: "custom" } })} />
      </SimpleGrid>
      {plan && (
        <Paper withBorder p="xs">
          <KeyValue
            rows={[
              ["Domain", `${fmt(plan.domain_max[0] - plan.domain_min[0], 2)} × ${fmt(plan.domain_max[1] - plan.domain_min[1], 2)} × ${fmt(plan.domain_max[2] - plan.domain_min[2], 2)} m`],
              ["Blockage ratio", plan.blockage_ratio !== null ? `${(plan.blockage_ratio * 100).toFixed(2)} %` : "—"],
              ["Background cells", `${plan.n_cells_block.join(" × ")} (${fmt(plan.base_cell, 3)} m)`],
              ["Ground plane z", `${fmt(plan.ground_z, 4)} m`],
            ]}
          />
          {plan.warnings.map((w, i) => (
            <Alert key={i} color="yellow" variant="light" p={4} mt={4}>
              <Text size="xs">{w}</Text>
            </Alert>
          ))}
        </Paper>
      )}

      <SectionTitle>Ground</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={op.ground} onChange={(v) => patchConfig({ operating: { ground: v } })}
        data={[{ value: "moving", label: "Moving (rolling road)" }, { value: "stationary", label: "Stationary" }]} />
      {op.ground === "moving" && (
        <Select size="xs" label="Moving ground under yaw" value={op.yaw_ground_mode} onChange={(v) => v && patchConfig({ operating: { yaw_ground_mode: v } })}
          data={[{ value: "road", label: "Road: ground moves along the vehicle axis at U∞·cos β" }, { value: "tunnel_belt", label: "Tunnel belt: belt moves with the freestream" }]} />
      )}
      <Text size="xs" c="dimmed">Tyre contact patch: the floor is raised into the tyres by a fraction of the rolling radius to avoid line contacts.</Text>
      <Slider size="sm" min={0} max={0.04} step={0.0025} value={t.contact_patch_fraction} label={(v) => `${(v * 100).toFixed(2)} % of R`}
        onChangeEnd={(v) => patchConfig({ tunnel: { contact_patch_fraction: v } })} marks={[{ value: 0.01, label: "1%" }, { value: 0.02, label: "2%" }]} mb="md" />

      <SectionTitle>Symmetry</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={t.symmetry} onChange={(v) => patchConfig({ tunnel: { symmetry: v } })}
        data={[{ value: "full", label: "Full vehicle" }, { value: "half", label: "Half (symmetry plane)" }]} />
      <ValidationList issues={project!.validation} filter={(i) => i.path.startsWith("tunnel")} />

      <SectionTitle>Far field and yaw</SectionTitle>
      <Select size="xs" label="Far-field boundary conditions" value={t.farfield_bc} onChange={(v) => v && patchConfig({ tunnel: { farfield_bc: v } })}
        data={[
          { value: "auto", label: "Automatic (inlet/outlet at 0°, freestream when yawed)" },
          { value: "inlet_outlet_slip", label: "Velocity inlet / pressure outlet / slip walls" },
          { value: "freestream", label: "Freestream on all far-field faces" },
        ]} />
      <Switch size="xs" mt={4} label="Size the domain and wake refinement for a yaw range (one mesh for a yaw sweep)"
        checked={!!yawRange} onChange={(e) => patchConfig({ tunnel: { design_yaw_range_deg: e.currentTarget.checked ? [-10, 10] : null } })} />
      {yawRange && (
        <SimpleGrid cols={2} spacing="xs">
          <NumField label="Yaw min" units="°" value={yawRange[0]} decimals={1} onCommit={(v) => patchConfig({ tunnel: { design_yaw_range_deg: [v, yawRange[1]] } })} />
          <NumField label="Yaw max" units="°" value={yawRange[1]} decimals={1} onCommit={(v) => patchConfig({ tunnel: { design_yaw_range_deg: [yawRange[0], v] } })} />
        </SimpleGrid>
      )}
    </Stack>
  );

  return (
    <PageLayout side={side}>
      <Paper withBorder radius="md" style={{ flex: 1, overflow: "hidden" }}>
        <Viewport3D onReady={(v) => (viewer.current = v)} />
      </Paper>
    </PageLayout>
  );
}
