/** Scientific colour maps (piecewise-linear control points, perceptually ordered). */
type Stop = [number, number, number, number]; // t, r, g, b in 0..1

const MAPS: Record<string, Stop[]> = {
  "Cool to Warm": [
    [0, 0.230, 0.299, 0.754],
    [0.25, 0.552, 0.690, 0.996],
    [0.5, 0.865, 0.865, 0.865],
    [0.75, 0.958, 0.604, 0.482],
    [1, 0.706, 0.016, 0.150],
  ],
  "Viridis (matplotlib)": [
    [0, 0.267, 0.005, 0.329],
    [0.25, 0.229, 0.322, 0.546],
    [0.5, 0.128, 0.567, 0.551],
    [0.75, 0.369, 0.789, 0.383],
    [1, 0.993, 0.906, 0.144],
  ],
  Turbo: [
    [0, 0.190, 0.072, 0.232],
    [0.13, 0.275, 0.408, 0.859],
    [0.25, 0.157, 0.666, 0.952],
    [0.38, 0.098, 0.886, 0.694],
    [0.5, 0.416, 0.988, 0.329],
    [0.63, 0.792, 0.937, 0.212],
    [0.75, 0.988, 0.729, 0.224],
    [0.88, 0.902, 0.341, 0.075],
    [1, 0.480, 0.016, 0.010],
  ],
  "Inferno (matplotlib)": [
    [0, 0.001, 0.000, 0.014],
    [0.25, 0.341, 0.062, 0.429],
    [0.5, 0.735, 0.216, 0.330],
    [0.75, 0.978, 0.557, 0.035],
    [1, 0.988, 0.998, 0.645],
  ],
  "Plasma (matplotlib)": [
    [0, 0.050, 0.030, 0.528],
    [0.25, 0.494, 0.012, 0.658],
    [0.5, 0.798, 0.280, 0.470],
    [0.75, 0.973, 0.585, 0.254],
    [1, 0.940, 0.975, 0.131],
  ],
  /** where shape zones allow change: plain surface grey to warm */
  "Shape freedom": [
    [0, 0.42, 0.45, 0.5],
    [0.35, 0.42, 0.56, 0.66],
    [0.7, 0.62, 0.72, 0.62],
    [1, 0.93, 0.78, 0.4],
  ],
  "Blue - Red": [
    [0, 0.0, 0.0, 1.0],
    [0.5, 1.0, 1.0, 1.0],
    [1, 1.0, 0.0, 0.0],
  ],
};

export const COLORMAPS = Object.keys(MAPS);

export function sampleColormap(name: string, t: number, out: Float32Array | number[], o = 0): void {
  const stops = MAPS[name] || MAPS["Cool to Warm"];
  if (!(t >= 0)) t = 0;
  if (t > 1) t = 1;
  let i = 1;
  while (i < stops.length - 1 && stops[i][0] < t) i++;
  const a = stops[i - 1];
  const b = stops[i];
  const f = (t - a[0]) / Math.max(1e-9, b[0] - a[0]);
  out[o] = a[1] + (b[1] - a[1]) * f;
  out[o + 1] = a[2] + (b[2] - a[2]) * f;
  out[o + 2] = a[3] + (b[3] - a[3]) * f;
}

export function colormapCss(name: string, steps = 16): string {
  const tmp = [0, 0, 0];
  const parts: string[] = [];
  for (let i = 0; i <= steps; i++) {
    sampleColormap(name, i / steps, tmp);
    parts.push(`rgb(${Math.round(tmp[0] * 255)},${Math.round(tmp[1] * 255)},${Math.round(tmp[2] * 255)}) ${(i / steps) * 100}%`);
  }
  return `linear-gradient(90deg, ${parts.join(", ")})`;
}

/** Vehicle parts: muted, mutually distinguishable tones on the dark viewport (body first). */
export const PART_COLORS = [0x9aa3ae, 0x7fa7c9, 0xc4a46b, 0x86b08f, 0xb98a8a, 0x9d90c0, 0x6fb0ae, 0xc9b67c, 0x8f9fb8, 0xa7b58a];
export const WHEEL_COLOR = 0x3c4149;

/**
 * Refinement level 0 (background cell) … 11: an ordered ramp from cool and dark to warm and
 * light, so a finer level always reads as "more". Shared by refinement boxes, the mesh cut
 * plane and their legends.
 */
export const LEVEL_COLORS = [0x3d4653, 0x445f78, 0x4e7c9c, 0x5d99b5, 0x74b2bd, 0x92c5b3, 0xb3d29e, 0xd5d58b, 0xe6c07b, 0xe8a46f, 0xe28867, 0xd86f68];

export function levelColorCss(level: number): string {
  const c = LEVEL_COLORS[Math.max(0, Math.min(LEVEL_COLORS.length - 1, Math.round(level)))];
  return "#" + c.toString(16).padStart(6, "0");
}
