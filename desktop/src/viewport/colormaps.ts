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

export const PART_COLORS = [0x9aa5b1, 0x4dabf7, 0xf59f00, 0x51cf66, 0xff6b6b, 0xcc5de8, 0x22b8cf, 0xfcc419, 0x845ef7, 0x20c997];
