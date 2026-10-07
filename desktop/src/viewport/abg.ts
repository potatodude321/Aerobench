/** Decoder for the engine's compact binary geometry payload (see aerobench/viz/payload.py). */
export interface AbgHeader {
  blocks: { name: string; dtype: string; count: number }[];
  n_points: number;
  part_names?: string[];
  scalar_range?: [number, number];
  [k: string]: any;
}

export interface Abg {
  header: AbgHeader;
  positions: Float32Array;
  indices?: Uint32Array;
  lines?: Uint32Array;
  /** wireframe line pairs (mesh views) */
  edges?: Uint32Array;
  scalars?: Float32Array;
  vectors?: Float32Array;
  partIds?: Uint16Array;
}

const SIZES: Record<string, number> = { float32: 4, uint32: 4, uint16: 2, int32: 4 };

export function decodeAbg(buf: ArrayBuffer): Abg {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== "ABG1") throw new Error("Not an ABG payload");
  const n = dv.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, n))) as AbgHeader;
  let off = 8 + n;
  const out: Abg = { header, positions: new Float32Array(0) };
  for (const b of header.blocks) {
    const size = (SIZES[b.dtype] || 4) * b.count;
    // copy to guarantee alignment for typed arrays
    const bytes = buf.slice(off, off + size);
    switch (b.name) {
      case "positions":
        out.positions = new Float32Array(bytes);
        break;
      case "indices":
        out.indices = new Uint32Array(bytes);
        break;
      case "lines":
        out.lines = new Uint32Array(bytes);
        break;
      case "edges":
        out.edges = new Uint32Array(bytes);
        break;
      case "scalars":
        out.scalars = new Float32Array(bytes);
        break;
      case "vectors":
        out.vectors = new Float32Array(bytes);
        break;
      case "part_ids":
        out.partIds = new Uint16Array(bytes);
        break;
    }
    off += size + ((4 - (size % 4)) % 4);
  }
  return out;
}
