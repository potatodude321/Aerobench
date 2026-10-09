import { Button, SegmentedControl, Select, SimpleGrid, Switch, Table, Text } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { ReactNode } from "react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { fmt } from "../components/ColorLegend";
import { notifyError } from "../components/notify";
import { Hint, KeyValue, NumField, PageLayout, SectionTitle, ValidationList } from "../components/ui";
import { pid, useApp } from "../state/store";

const MODELS = [
  { value: "kOmegaSST", label: "k-ω SST (recommended)" },
  { value: "SpalartAllmaras", label: "Spalart–Allmaras" },
  { value: "realizableKE", label: "Realizable k-ε" },
  { value: "GEKO", label: "GEKO (v2606, advanced)" },
  { value: "laminar", label: "Laminar (not for vehicles)" },
];

export function PhysicsPage() {
  const { project, patchConfig, setSection } = useApp();
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
    <>
      <SectionTitle first>Driving condition</SectionTitle>
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Speed U∞" units="m/s" value={op.speed} decimals={3} min={0.1} onCommit={(v) => patchConfig({ operating: { speed: v } })}
          description={`${fmt(op.speed * 3.6, 1)} km/h, ${fmt(op.speed * 2.23694, 1)} mph`} />
        <NumField label="Yaw β" units="°" value={op.yaw_deg} decimals={2} min={-30} max={30} onCommit={(v) => patchConfig({ operating: { yaw_deg: v } })}
          description="Positive: wind from the right" />
      </SimpleGrid>
      <Switch size="xs" mt={10} label="Rotating wheels" checked={op.wheels_rotate} onChange={(e) => patchConfig({ operating: { wheels_rotate: e.currentTarget.checked } })} />
      <Hint style={{ marginTop: 6 }}>
        Ground: {op.ground === "moving" ? `moving (${op.yaw_ground_mode.replace("_", " ")})` : "stationary"}, set on the Tunnel page.
      </Hint>

      <SectionTitle>Air</SectionTitle>
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
      <Hint style={{ marginTop: 6 }}>Setting the temperature fills in density and viscosity for dry air at sea level; either can then be changed.</Hint>

      <SectionTitle>Turbulence</SectionTitle>
      <Select size="xs" label="Model" data={MODELS} value={ph.turbulence_model} onChange={(v) => v && patchConfig({ physics: { turbulence_model: v } })} />
      <Text size="xs" c="dimmed" fw={500} mt={10} mb={4}>
        Near the walls
      </Text>
      <SegmentedControl size="xs" fullWidth value={ph.wall_treatment} onChange={(v) => patchConfig({ physics: { wall_treatment: v } })}
        data={[{ value: "wall_functions", label: "Wall functions (y+ 30–300)" }, { value: "low_re", label: "Resolved (y+ ≈ 1)" }]} />
      <SimpleGrid cols={2} spacing="xs" mt={10}>
        <NumField label="Inflow intensity" units="%" value={ph.inlet_turbulence.intensity * 100} decimals={2}
          onCommit={(v) => patchConfig({ physics: { inlet_turbulence: { intensity: v / 100 } } })} />
        {ph.inlet_turbulence.spec === "viscosity_ratio" ? (
          <NumField label="Viscosity ratio νt/ν" value={ph.inlet_turbulence.viscosity_ratio} decimals={2}
            onCommit={(v) => patchConfig({ physics: { inlet_turbulence: { viscosity_ratio: v } } })} />
        ) : (
          <NumField label="Length scale" units="m" value={ph.inlet_turbulence.length_scale} decimals={4}
            onCommit={(v) => patchConfig({ physics: { inlet_turbulence: { length_scale: v } } })} />
        )}
      </SimpleGrid>
      <SegmentedControl size="xs" mt={8} value={ph.inlet_turbulence.spec}
        onChange={(v) => patchConfig({ physics: { inlet_turbulence: { spec: v, length_scale: v === "length_scale" ? ph.inlet_turbulence.length_scale ?? derived?.turbulence?.length_scale : null } } })}
        data={[{ value: "viscosity_ratio", label: "Give viscosity ratio" }, { value: "length_scale", label: "Give length scale" }]} />

      <SectionTitle>Solver settings</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={nu.preset} onChange={setNumericsPreset}
        data={[{ value: "robust", label: "Robust" }, { value: "balanced", label: "Balanced" }, { value: "fast_convergence", label: "Fast" }, { value: "high_fidelity", label: "High fidelity" }]} />
      {presetInfo && <Hint style={{ marginTop: 6 }}>{presetInfo.description} (preset version {nu.preset_version})</Hint>}
      <SimpleGrid cols={2} spacing="xs" mt={10}>
        <NumField label="Iterations" value={nu.iterations} decimals={0} min={10} onCommit={(v) => patchConfig({ numerics: { iterations: Math.round(v) } })} description="In total" />
        <NumField label="First-order iterations" value={nu.first_order_iterations ?? presetInfo?.first_order_iterations ?? 0} decimals={0}
          onCommit={(v) => patchConfig({ numerics: { first_order_iterations: Math.round(v) } })} description="Robust start" />
        <NumField label="Averaged at the end" units="%" value={nu.averaging_fraction * 100} decimals={0}
          onCommit={(v) => patchConfig({ numerics: { averaging_fraction: v / 100 } })} description="Of all iterations" />
        <NumField label="Save every" value={nu.write_interval ?? Math.min(500, nu.iterations)} decimals={0}
          onCommit={(v) => patchConfig({ numerics: { write_interval: Math.round(v) } })} description="Iterations (restart points)" />
      </SimpleGrid>
      <Switch size="xs" mt={10} label="Start from a potential-flow solution" checked={nu.potential_init} onChange={(e) => patchConfig({ numerics: { potential_init: e.currentTarget.checked } })} />
      <Switch size="xs" mt={8} label="Stop as soon as the run has converged" checked={!!nu.stop_when_converged}
        onChange={(e) => patchConfig({ numerics: { stop_when_converged: e.currentTarget.checked } })} />
      <Hint style={{ marginTop: 4 }}>Ends the run (state saved) once every convergence criterion is met, instead of running all iterations.</Hint>

      <SectionTitle>Extra results to save</SectionTitle>
      <SimpleGrid cols={2} spacing={8}>
        {[
          ["component_forces", "Forces per part"],
          ["yplus", "y+ on walls"],
          ["wall_shear_stress", "Wall shear stress"],
          ["pressure_coefficient", "Cp field"],
          ["vorticity", "Vorticity"],
          ["q_criterion", "Q-criterion"],
          ["continuity", "Continuity error"],
        ].map(([k, label]) => (
          <Switch key={k} size="xs" label={label} checked={!!cfg.observables[k]} onChange={(e) => patchConfig({ observables: { [k]: e.currentTarget.checked } })} />
        ))}
      </SimpleGrid>
      <ValidationList issues={project!.validation} filter={(i) => /physics|mesh.layers|operating/.test(i.path)} />
    </>
  );

  return (
    <PageLayout
      side={side}
      title="Physics"
      lede="How fast the car drives, the air it drives through, and how the solver treats turbulence."
      footer={
        <Button fullWidth variant="default" rightSection={<IconArrowRight size={15} />} onClick={() => setSection("run")}>
          Continue to run
        </Button>
      }
    >
      <div style={{ flex: 1, overflow: "auto", padding: "28px 36px" }}>
        <div style={{ maxWidth: 980 }}>
          <h2 className="ab-page-title" style={{ fontSize: 19 }}>
            What follows from these settings
          </h2>
          <p className="ab-page-lede" style={{ maxWidth: 640 }}>
            Calculated from the inputs on the left, so you can check them before running. Nothing here needs to be entered.
          </p>
          {derived && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))", columnGap: 48 }}>
              <Block title="Flow">
                <KeyValue
                  rows={[
                    ["Reynolds number (vehicle length)", fmt(derived.reynolds_length, 0)],
                    ["Reynolds number (reference length)", fmt(derived.reynolds_ref, 0)],
                    ["Dynamic pressure q", `${fmt(derived.dynamic_pressure_pa, 1)} Pa`],
                    ["Mach number", fmt(derived.mach, 3)],
                    ["Free-stream velocity", derived.freestream_vector.map((x: number) => fmt(x, 3)).join(", ") + " m/s"],
                  ]}
                />
                {derived.mach_warning && <Hint style={{ color: "var(--ab-warn)", marginTop: 6 }}>{derived.mach_warning}</Hint>}
              </Block>
              <Block title="Turbulence entering the tunnel">
                <KeyValue
                  rows={[
                    ["k", `${fmt(derived.turbulence.k, 5)} m²/s²`],
                    ["ω", `${fmt(derived.turbulence.omega, 4)} 1/s`],
                    ["ε", `${fmt(derived.turbulence.epsilon, 5)} m²/s³`],
                    ["νt", `${fmt(derived.turbulence.nut, 6)} m²/s`],
                    ["Length scale", `${fmt(derived.turbulence.length_scale, 5)} m`],
                  ]}
                />
                <Hint style={{ marginTop: 6 }}>k = 1.5 (I·U)²; ω from the viscosity ratio or the length scale.</Hint>
              </Block>
              <Block title={`Wall spacing for y+ = ${fmt(derived.wall_spacing.target_yplus, 0)}`}>
                <KeyValue
                  rows={[
                    ["Skin friction Cf", fmt(derived.wall_spacing.cf, 5)],
                    ["Friction velocity uτ", `${fmt(derived.wall_spacing.friction_velocity, 3)} m/s`],
                    ["First cell centre height", `${fmt(derived.wall_spacing.first_cell_centre_height * 1000, 4)} mm`],
                    ["First cell thickness", `${fmt(derived.wall_spacing.first_cell_height * 1000, 4)} mm`],
                  ]}
                />
                <Hint style={{ marginTop: 6 }}>{derived.wall_spacing.assumptions}</Hint>
              </Block>
              {derived.wheels && (
                <Block title="Moving ground and wheels">
                  <Text size="xs" mb={6}>
                    Ground speed {fmt(derived.ground_speed, 3)} m/s
                  </Text>
                  <Table className="compact" withRowBorders={false}>
                    <Table.Tbody>
                      {derived.wheels.map((w: any) => (
                        <Table.Tr key={w.position}>
                          <Table.Td>{w.position}</Table.Td>
                          <Table.Td>R {fmt(w.radius, 4)} m</Table.Td>
                          <Table.Td>{fmt(w.omega_rad_s, 2)} rad/s</Table.Td>
                          <Table.Td>{fmt(w.rpm, 0)} rpm</Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                  <Hint style={{ marginTop: 6 }}>Rotating-wall approximation: the tyre and rim surfaces move at ω×r. No rotating mesh.</Hint>
                </Block>
              )}
            </div>
          )}
          <Block title="Sign conventions">
            <Text size="xs" c="dimmed" maw={720} lh={1.6}>
              Drag positive rearward; lift positive upward (downforce = −lift); side force positive to the vehicle's right; pitching moment
              positive nose-up; yawing moment positive nose-right. Aero balance = front downforce ÷ total downforce.
            </Text>
          </Block>
        </div>
      </div>
    </PageLayout>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <SectionTitle>{title}</SectionTitle>
      {children}
    </section>
  );
}
