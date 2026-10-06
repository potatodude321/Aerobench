import { Alert, Badge, Card, Group, Paper, SegmentedControl, Select, SimpleGrid, Stack, Switch, Table, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { fmt } from "../components/ColorLegend";
import { notifyError } from "../components/notify";
import { KeyValue, NumField, PageLayout, SectionTitle, ValidationList } from "../components/ui";
import { pid, useApp } from "../state/store";

const MODELS = [
  { value: "kOmegaSST", label: "k-ω SST (recommended)" },
  { value: "SpalartAllmaras", label: "Spalart–Allmaras" },
  { value: "realizableKE", label: "Realizable k-ε" },
  { value: "GEKO", label: "GEKO (v2606, advanced)" },
  { value: "laminar", label: "Laminar (not for vehicles)" },
];

export function PhysicsPage() {
  const { project, patchConfig } = useApp();
  const cfg = project!.config;
  const op = cfg.operating;
  const ph = cfg.physics;
  const nu = cfg.numerics;
  const [derived, setDerived] = useState<any>(null);
  const [presets, setPresets] = useState<any>(null);

  useEffect(() => {
    api.get(`/api/p/${pid()}/derived`).then(setDerived).catch(notifyError);
  }, [project?.config.modified_at]);
  useEffect(() => {
    api.get("/api/numerics-presets").then(setPresets).catch(notifyError);
  }, []);

  const setNumericsPreset = async (p: string) => {
    try {
      await api.post(`/api/p/${pid()}/presets/numerics/${p}`);
      await useApp.getState().refreshProject();
    } catch (e) {
      notifyError(e);
    }
  };

  const presetInfo = presets?.[nu.preset_version]?.[nu.preset === "custom" ? nu.base_preset : nu.preset];
  const side = (
    <Stack gap="xs">
      <SectionTitle>Operating condition</SectionTitle>
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Speed U∞" units="m/s" value={op.speed} decimals={3} min={0.1} onCommit={(v) => patchConfig({ operating: { speed: v } })}
          description={`${fmt(op.speed * 3.6, 1)} km/h · ${fmt(op.speed * 2.23694, 1)} mph`} />
        <NumField label="Yaw β" units="°" value={op.yaw_deg} decimals={2} min={-30} max={30} onCommit={(v) => patchConfig({ operating: { yaw_deg: v } })}
          description="+ = wind from the vehicle's right" />
      </SimpleGrid>
      <Switch size="xs" label="Rotate wheels (rotating-wall approximation)" checked={op.wheels_rotate} onChange={(e) => patchConfig({ operating: { wheels_rotate: e.currentTarget.checked } })} />
      <Text size="xs" c="dimmed">
        Ground: {op.ground === "moving" ? `moving (${op.yaw_ground_mode.replace("_", " ")})` : "stationary"} — change in Tunnel.
      </Text>

      <SectionTitle>Fluid</SectionTitle>
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Temperature" units="°C" value={ph.fluid.temperature_c} decimals={1}
          onCommit={async (t) => {
            try {
              const air = await api.get(`/api/air?temperature_c=${t}`);
              patchConfig({ physics: { fluid: { temperature_c: t, density: air.density, kinematic_viscosity: air.kinematic_viscosity } } });
            } catch (e) {
              notifyError(e);
            }
          }} />
        <NumField label="Density" units="kg/m³" value={ph.fluid.density} decimals={4} onCommit={(v) => patchConfig({ physics: { fluid: { density: v } } })} />
      </SimpleGrid>
      <NumField label="Kinematic viscosity" units="m²/s" value={ph.fluid.kinematic_viscosity} decimals={9} onCommit={(v) => patchConfig({ physics: { fluid: { kinematic_viscosity: v } } })} />
      <Text size="xs" c="dimmed">Changing the temperature updates density and viscosity (dry air at 1 atm); either can then be overridden.</Text>

      <SectionTitle>Turbulence</SectionTitle>
      <Select size="xs" data={MODELS} value={ph.turbulence_model} onChange={(v) => v && patchConfig({ physics: { turbulence_model: v } })} />
      <SegmentedControl size="xs" fullWidth value={ph.wall_treatment} onChange={(v) => patchConfig({ physics: { wall_treatment: v } })}
        data={[{ value: "wall_functions", label: "Wall functions (y+ 30–300)" }, { value: "low_re", label: "Resolved wall (y+ ≈ 1)" }]} />
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Inlet intensity" units="%" value={ph.inlet_turbulence.intensity * 100} decimals={2}
          onCommit={(v) => patchConfig({ physics: { inlet_turbulence: { intensity: v / 100 } } })} />
        {ph.inlet_turbulence.spec === "viscosity_ratio" ? (
          <NumField label="Viscosity ratio νt/ν" value={ph.inlet_turbulence.viscosity_ratio} decimals={2}
            onCommit={(v) => patchConfig({ physics: { inlet_turbulence: { viscosity_ratio: v } } })} />
        ) : (
          <NumField label="Length scale" units="m" value={ph.inlet_turbulence.length_scale} decimals={4}
            onCommit={(v) => patchConfig({ physics: { inlet_turbulence: { length_scale: v } } })} />
        )}
      </SimpleGrid>
      <SegmentedControl size="xs" value={ph.inlet_turbulence.spec}
        onChange={(v) => patchConfig({ physics: { inlet_turbulence: { spec: v, length_scale: v === "length_scale" ? ph.inlet_turbulence.length_scale ?? derived?.turbulence?.length_scale : null } } })}
        data={[{ value: "viscosity_ratio", label: "Viscosity ratio" }, { value: "length_scale", label: "Length scale" }]} />

      <SectionTitle>Numerics</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={nu.preset} onChange={setNumericsPreset}
        data={[{ value: "robust", label: "Robust" }, { value: "balanced", label: "Balanced" }, { value: "fast_convergence", label: "Fast conv." }, { value: "high_fidelity", label: "High fidelity" }]} />
      {presetInfo && (
        <Text size="xs" c="dimmed">
          {presetInfo.description} <Badge size="xs" variant="outline">preset v{nu.preset_version}</Badge>
        </Text>
      )}
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Iterations" value={nu.iterations} decimals={0} min={10} onCommit={(v) => patchConfig({ numerics: { iterations: Math.round(v) } })} description="Total, including first-order" />
        <NumField label="First-order iterations" value={nu.first_order_iterations ?? presetInfo?.first_order_iterations ?? 0} decimals={0}
          onCommit={(v) => patchConfig({ numerics: { first_order_iterations: Math.round(v) } })} description="Robust start, then 2nd order" />
        <NumField label="Averaging window" units="%" value={nu.averaging_fraction * 100} decimals={0}
          onCommit={(v) => patchConfig({ numerics: { averaging_fraction: v / 100 } })} description="Final share of iterations averaged" />
        <NumField label="Write interval" value={nu.write_interval ?? Math.min(500, nu.iterations)} decimals={0}
          onCommit={(v) => patchConfig({ numerics: { write_interval: Math.round(v) } })} description="Restart points" />
      </SimpleGrid>
      <Switch size="xs" label="Initialise with potential flow (potentialFoam)" checked={nu.potential_init} onChange={(e) => patchConfig({ numerics: { potential_init: e.currentTarget.checked } })} />

      <SectionTitle>Observables</SectionTitle>
      <SimpleGrid cols={2} spacing={4}>
        {[
          ["component_forces", "Per-component forces"],
          ["yplus", "y+ field"],
          ["wall_shear_stress", "Wall shear stress"],
          ["pressure_coefficient", "Cp field"],
          ["vorticity", "Vorticity field"],
          ["q_criterion", "Q-criterion field"],
          ["continuity", "Continuity error"],
        ].map(([k, label]) => (
          <Switch key={k} size="xs" label={label} checked={!!cfg.observables[k]} onChange={(e) => patchConfig({ observables: { [k]: e.currentTarget.checked } })} />
        ))}
      </SimpleGrid>
      <ValidationList issues={project!.validation} filter={(i) => /physics|mesh.layers|operating/.test(i.path)} />
    </Stack>
  );

  return (
    <PageLayout side={side}>
      <Paper withBorder radius="md" p="md" style={{ flex: 1, overflow: "auto" }}>
        <Text fw={700} mb="xs">
          What the software derives from your inputs
        </Text>
        {derived && (
          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
            <Card withBorder>
              <Text size="sm" fw={600} mb={4}>
                Flow
              </Text>
              <KeyValue
                rows={[
                  ["Reynolds number (vehicle length)", fmt(derived.reynolds_length, 0)],
                  ["Reynolds number (reference length)", fmt(derived.reynolds_ref, 0)],
                  ["Dynamic pressure q", `${fmt(derived.dynamic_pressure_pa, 1)} Pa`],
                  ["Mach number", fmt(derived.mach, 3)],
                  ["Freestream vector", derived.freestream_vector.map((x: number) => fmt(x, 3)).join(", ") + " m/s"],
                ]}
              />
              {derived.mach_warning && (
                <Alert color="yellow" variant="light" p={4} mt={4}>
                  <Text size="xs">{derived.mach_warning}</Text>
                </Alert>
              )}
            </Card>
            <Card withBorder>
              <Text size="sm" fw={600} mb={4}>
                Freestream turbulence
              </Text>
              <KeyValue
                rows={[
                  ["k", `${fmt(derived.turbulence.k, 5)} m²/s²`],
                  ["ω", `${fmt(derived.turbulence.omega, 4)} 1/s`],
                  ["ε", `${fmt(derived.turbulence.epsilon, 5)} m²/s³`],
                  ["νt", `${fmt(derived.turbulence.nut, 6)} m²/s`],
                  ["Length scale", `${fmt(derived.turbulence.length_scale, 5)} m`],
                ]}
              />
              <Text size="xs" c="dimmed" mt={4}>
                k = 1.5 (I·U)²; ω from the viscosity ratio or length scale.
              </Text>
            </Card>
            <Card withBorder>
              <Text size="sm" fw={600} mb={4}>
                Near-wall spacing for target y+ = {fmt(derived.wall_spacing.target_yplus, 0)}
              </Text>
              <KeyValue
                rows={[
                  ["Skin friction Cf", fmt(derived.wall_spacing.cf, 5)],
                  ["Friction velocity uτ", `${fmt(derived.wall_spacing.friction_velocity, 3)} m/s`],
                  ["First cell centre height", `${fmt(derived.wall_spacing.first_cell_centre_height * 1000, 4)} mm`],
                  ["First cell thickness", `${fmt(derived.wall_spacing.first_cell_height * 1000, 4)} mm`],
                ]}
              />
              <Text size="xs" c="dimmed" mt={4}>
                {derived.wall_spacing.assumptions}
              </Text>
            </Card>
            {derived.wheels && (
              <Card withBorder>
                <Text size="sm" fw={600} mb={4}>
                  Moving ground and wheels
                </Text>
                <Text size="xs" mb={4}>
                  Ground speed {fmt(derived.ground_speed, 3)} m/s
                </Text>
                <Table className="compact">
                  <Table.Tbody>
                    {derived.wheels.map((w: any) => (
                      <Table.Tr key={w.position}>
                        <Table.Td>{w.position}</Table.Td>
                        <Table.Td>R {fmt(w.radius, 4)} m</Table.Td>
                        <Table.Td>ω {fmt(w.omega_rad_s, 2)} rad/s</Table.Td>
                        <Table.Td>{fmt(w.rpm, 0)} rpm</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
                <Text size="xs" c="dimmed" mt={4}>
                  Rotating-wall approximation: tangential wall velocity ω×r on the tyre/rim surfaces; no MRF or sliding mesh.
                </Text>
              </Card>
            )}
          </SimpleGrid>
        )}
        <Group mt="md">
          <Text size="xs" c="dimmed">
            Sign conventions: drag + rearward, lift + up (downforce = −lift), side force + to the vehicle's right, pitching moment + nose up,
            yawing moment + nose right. Aero balance = front downforce / total downforce.
          </Text>
        </Group>
      </Paper>
    </PageLayout>
  );
}
