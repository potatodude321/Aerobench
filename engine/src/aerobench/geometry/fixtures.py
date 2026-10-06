"""Synthetic, procedurally generated geometry for tests, benchmarks and demos.

These shapes are generated from published dimensions or invented proportions — they
contain no third-party geometry and can be redistributed freely.  Every generator returns
a watertight, outward-oriented :class:`TriSurface` with named regions.

* :func:`ahmed_body` — Ahmed et al. (1984) reference body (dimensions below), with stilts.
* :func:`generic_car` — an invented saloon-like body with four wheels and optional
  rear wing (clearly *synthetic*: not a real vehicle).
* :func:`sphere`, :func:`box`, :func:`cylinder` — primitives for the validation ladder.
"""

from __future__ import annotations

import math

import numpy as np

from .trisurface import TriSurface


# ------------------------------------------------------------------------------ helpers


def _loft(rings: np.ndarray, cap_start: str = "fan", cap_end: str = "fan") -> tuple[np.ndarray, np.ndarray]:
    """Stitch K rings of M points (K, M, 3) into a closed surface.

    Ring point order must be counter-clockwise when viewed from the start cap looking
    along the loft direction... orientation is fixed afterwards by the caller via the
    signed volume, so only consistency matters.
    """
    K, M, _ = rings.shape
    pts = rings.reshape(-1, 3)
    faces = []
    for k in range(K - 1):
        a = k * M + np.arange(M)
        b = k * M + (np.arange(M) + 1) % M
        c = (k + 1) * M + (np.arange(M) + 1) % M
        d = (k + 1) * M + np.arange(M)
        faces.append(np.column_stack([a, b, c]))
        faces.append(np.column_stack([a, c, d]))
    faces = np.vstack(faces)
    extra = []
    n = len(pts)
    for _cap, ring_idx, rev in ((cap_start, 0, True), (cap_end, K - 1, False)):
        idx = ring_idx * M + np.arange(M)
        centre = rings[ring_idx].mean(axis=0)
        extra.append(centre)
        ci = n + len(extra) - 1
        f = np.column_stack([idx, np.roll(idx, -1), np.full(M, ci)])
        if rev:
            f = f[:, ::-1]
        faces = np.vstack([faces, f])
    pts = np.vstack([pts, np.array(extra)])
    return pts, faces


def _orient_outward(pts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    t = pts[faces]
    vol = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()
    return faces if vol > 0 else faces[:, ::-1].copy()


def _surface(pts, faces, name) -> TriSurface:
    faces = _orient_outward(pts, faces)
    return TriSurface(pts, faces, np.zeros(len(faces), np.int32), [name])


def _rect_ring(y0, y1, z0, z1, n_y: int, n_z: int, x: float) -> np.ndarray:
    """Closed rectangular ring (counter-clockwise in the y-z plane)."""
    ys = np.linspace(y0, y1, n_y + 1)
    zs = np.linspace(z0, z1, n_z + 1)
    bottom = np.column_stack([ys[:-1], np.full(n_y, z0)])
    right = np.column_stack([np.full(n_z, y1), zs[:-1]])
    top = np.column_stack([ys[::-1][:-1], np.full(n_y, z1)])
    left = np.column_stack([np.full(n_z, y0), zs[::-1][:-1]])
    yz = np.vstack([bottom, right, top, left])
    return np.column_stack([np.full(len(yz), x), yz])


def _rect_cap(y0, y1, z0, z1, n_y, n_z, x, ring_offset: int, flip: bool) -> tuple[np.ndarray, np.ndarray]:
    """Structured cap matching :func:`_rect_ring` boundary. Returns (interior points, faces).

    Faces index into: ring points (ring_offset + i) and new interior points (appended)."""
    ys = np.linspace(y0, y1, n_y + 1)
    zs = np.linspace(z0, z1, n_z + 1)
    M = 2 * (n_y + n_z)
    # map grid (i,j) -> global index; boundary from ring, interior new
    idx = -np.ones((n_y + 1, n_z + 1), np.int64)
    k = 0
    for i in range(n_y):
        idx[i, 0] = ring_offset + k; k += 1
    for j in range(n_z):
        idx[n_y, j] = ring_offset + k; k += 1
    for i in range(n_y, 0, -1):
        idx[i, n_z] = ring_offset + k; k += 1
    for j in range(n_z, 0, -1):
        idx[0, j] = ring_offset + k; k += 1
    assert k == M
    interior = []
    for i in range(1, n_y):
        for j in range(1, n_z):
            interior.append([x, ys[i], zs[j]])
    return np.array(interior).reshape(-1, 3), idx


def _assemble_rect_loft(stations: list[tuple[float, float, float, float, float]], n_y: int, n_z: int) -> tuple[np.ndarray, np.ndarray]:
    """Loft rectangular sections (x, y0, y1, z0, z1) with structured end caps (watertight)."""
    rings = np.array([_rect_ring(y0, y1, z0, z1, n_y, n_z, x) for x, y0, y1, z0, z1 in stations])
    K, M, _ = rings.shape
    pts = rings.reshape(-1, 3)
    faces = []
    for k in range(K - 1):
        a = k * M + np.arange(M)
        b = k * M + (np.arange(M) + 1) % M
        c = (k + 1) * M + (np.arange(M) + 1) % M
        d = (k + 1) * M + np.arange(M)
        faces.append(np.column_stack([a, b, c]))
        faces.append(np.column_stack([a, c, d]))
    all_pts = [pts]
    n = len(pts)
    for which, (x, y0, y1, z0, z1) in ((0, stations[0]), (K - 1, stations[-1])):
        interior, idx = _rect_cap(y0, y1, z0, z1, n_y, n_z, x, which * M, flip=which == 0)
        # assign interior indices
        q = 0
        for i in range(1, n_y):
            for j in range(1, n_z):
                idx[i, j] = n + q
                q += 1
        n += len(interior)
        all_pts.append(interior)
        cf = []
        for i in range(n_y):
            for j in range(n_z):
                a, b, c, d = idx[i, j], idx[i + 1, j], idx[i + 1, j + 1], idx[i, j + 1]
                cf.append([a, b, c])
                cf.append([a, c, d])
        cf = np.array(cf)
        if which == 0:
            cf = cf[:, ::-1]
        faces.append(cf)
    return np.vstack(all_pts), np.vstack(faces)


# ------------------------------------------------------------------------------ primitives


def sphere(radius: float = 0.5, centre=(0.0, 0.0, 0.0), n_lat: int = 48, n_lon: int = 96, name: str = "sphere") -> TriSurface:
    th = np.linspace(0, math.pi, n_lat + 1)[1:-1]
    ph = np.linspace(0, 2 * math.pi, n_lon, endpoint=False)
    rings = np.array([[[radius * math.cos(t), radius * math.sin(t) * math.cos(p), radius * math.sin(t) * math.sin(p)] for p in ph] for t in th])
    pts, faces = _loft(rings)
    # replace cap centroids by true poles
    pts[-2] = [radius, 0, 0]
    pts[-1] = [-radius, 0, 0]
    # cap_start is ring 0 at theta small => x ~ +r ; centre index -2 is start cap
    return _surface(pts + np.asarray(centre), faces, name)


def cylinder(radius: float, p1, p2, n: int = 64, n_len: int = 1, name: str = "cylinder") -> TriSurface:
    p1, p2 = np.asarray(p1, float), np.asarray(p2, float)
    ax = p2 - p1
    L = np.linalg.norm(ax)
    ax /= L
    tmp = np.array([1.0, 0, 0]) if abs(ax[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(ax, tmp)
    u /= np.linalg.norm(u)
    v = np.cross(ax, u)
    ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
    rings = np.array([[p1 + ax * (L * s) + radius * (math.cos(a) * u + math.sin(a) * v) for a in ang] for s in np.linspace(0, 1, n_len + 1)])
    pts, faces = _loft(rings)
    return _surface(pts, faces, name)


def box(lo, hi, n: int = 8, name: str = "box") -> TriSurface:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    xs = np.linspace(lo[0], hi[0], n + 1)
    stations = [(x, lo[1], hi[1], lo[2], hi[2]) for x in xs]
    pts, faces = _assemble_rect_loft(stations, n, n)
    return _surface(pts, faces, name)


# ------------------------------------------------------------------------------ Ahmed body

AHMED = {
    "length": 1.044,
    "width": 0.389,
    "height": 0.288,
    "front_radius": 0.100,
    "slant_length": 0.222,
    "clearance": 0.050,
    "stilt_diameter": 0.030,
    # stilt x positions measured from the nose; lateral half-spacing (approximate — stilts
    # are a secondary feature; record as such in benchmark metadata)
    "stilt_x": (0.163, 0.633),
    "stilt_y": 0.1195,
}


def ahmed_body(slant_deg: float = 25.0, n_front: int = 16, n_y: int = 24, n_z: int = 18, stilts: bool = True,
               stilt_overlap: float = 0.01) -> TriSurface:
    """Ahmed body (Ahmed, Ramm & Faltin, SAE 840300, 1984) in the CFD frame.

    The body is the intersection of the rounded side profile extruded laterally and the
    rounded plan profile extruded vertically (sharp longitudinal edges), nose at x=0
    pointing to -x, ground at z=0.  Stilts extend ``stilt_overlap`` below the ground so
    the tunnel floor cuts them cleanly.
    """
    A = AHMED
    L, W, H, R, ls, c = A["length"], A["width"], A["height"], A["front_radius"], A["slant_length"], A["clearance"]
    phi = math.radians(slant_deg)
    x_slant = L - ls * math.cos(phi)
    # stations: dense cosine-spaced through the front radius, then key x positions
    s = (1 - np.cos(np.linspace(0, math.pi / 2, n_front + 1)))  # 0..1
    xf = R * s
    xf[0] = 0.0
    mid = np.linspace(R, x_slant, 12)[1:]
    rear = np.linspace(x_slant, L, 8)[1:]
    xs = np.concatenate([xf, mid, rear])

    def rho(x):
        if x >= R:
            return R
        return math.sqrt(max(R * R - (R - x) ** 2, 0.0))

    stations = []
    for x in xs:
        r = rho(x)
        hw = W / 2 - R + r
        zb = c + R - r
        zt = c + H - R + r
        if x > x_slant:
            zt = c + H - (x - x_slant) * math.tan(phi)
        stations.append((x, -hw, hw, zb, zt))
    pts, faces = _assemble_rect_loft(stations, n_y, n_z)
    body = _surface(pts, faces, "body")
    parts = [body]
    if stilts:
        d = A["stilt_diameter"]
        for i, sx in enumerate(A["stilt_x"]):
            for j, sy in enumerate((-A["stilt_y"], A["stilt_y"])):
                cyl = cylinder(d / 2, (sx, sy, -stilt_overlap), (sx, sy, c + 0.01), n=24, name=f"stilt{2 * i + j}")
                cyl.region_names = ["stilts"]
                parts.append(cyl)
    out = TriSurface.merge(parts)
    # shift so the nose tip is at x=0 pointing -x: the profile already starts at x=0 and
    # extends to +x, i.e. nose at x=0 and tail at +x.  Correct for the CFD frame.
    return out


# ------------------------------------------------------------------------------ generic car


def naca4(code: str = "4412", n: int = 60) -> np.ndarray:
    """Closed NACA 4-digit section (x in [0,1], z) as a (2n, 2) loop, cosine spacing."""
    m = int(code[0]) / 100
    p = int(code[1]) / 10
    t = int(code[2:]) / 100
    beta = np.linspace(0, math.pi, n + 1)
    x = (1 - np.cos(beta)) / 2
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)  # closed TE
    if p > 0:
        yc = np.where(x < p, m / p**2 * (2 * p * x - x**2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x**2))
        dyc = np.where(x < p, 2 * m / p**2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    else:
        yc = np.zeros_like(x)
        dyc = np.zeros_like(x)
    th = np.arctan(dyc)
    xu, zu = x - yt * np.sin(th), yc + yt * np.cos(th)
    xl, zl = x + yt * np.sin(th), yc - yt * np.cos(th)
    upper = np.column_stack([xu, zu])[::-1]  # TE -> LE
    lower = np.column_stack([xl, zl])[1:-1]  # LE -> TE (exclude duplicates)
    return np.vstack([upper, lower])


def wing(chord: float, span: float, le_point, angle_deg: float = 0.0, code: str = "4412", n: int = 50,
         n_span: int = 8, inverted: bool = True, name: str = "wing") -> TriSurface:
    """Straight wing along y.  ``angle_deg`` is the angle of attack (+ve: trailing edge down
    for an inverted (downforce) wing, i.e. more downforce)."""
    sec = naca4(code, n) * chord  # (x, z) local, LE at origin
    if inverted:
        sec[:, 1] *= -1
    a = math.radians(angle_deg)
    # rotate about the leading edge: positive angle lowers the trailing edge
    rot = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
    sec = sec @ rot.T
    le = np.asarray(le_point, float)
    ys = np.linspace(-span / 2, span / 2, n_span + 1)
    rings = np.array([[[le[0] + px, le[1] + y, le[2] + pz] for px, pz in sec] for y in ys])
    pts, faces = _loft(rings)
    return _surface(pts, faces, name)


def generic_car(
    length: float = 4.5,
    width: float = 1.8,
    height: float = 1.4,
    ride_height: float = 0.15,
    wheel_radius: float = 0.32,
    wheel_width: float = 0.22,
    wheelbase: float = 2.7,
    front_overhang: float = 0.85,
    track: float = 1.55,
    rear_wing: bool = False,
    wing_angle_deg: float = 4.0,
    n_x: int = 72,
    n_theta: int = 64,
    wheel_sinkage: float = 0.0,
) -> TriSurface:
    """An invented saloon-like body (superelliptic sections) with four separate wheels.

    Regions: ``body``, ``wheel_FL``, ``wheel_FR``, ``wheel_RL``, ``wheel_RR`` and,
    optionally, ``wing``.  Nose at x=0 (pointing -x), ground at z=0, centreline y=0.
    Wheels touch the ground at z = -wheel_sinkage.
    """
    L, W, Hh = length, width, height
    xs = (1 - np.cos(np.linspace(0, math.pi, n_x))) / 2 * L  # cosine clustering at ends

    def smoothstep(t):
        t = np.clip(t, 0, 1)
        return t * t * (3 - 2 * t)

    def z_bottom(x):
        u = x / L
        zb = ride_height + 0.10 * (1 - smoothstep(u / 0.12)) * (1 - u)  # rounded nose underside
        zb += 0.12 * smoothstep((u - 0.86) / 0.14)  # diffuser upsweep
        return zb

    def z_top(x):
        u = x / L
        bonnet = ride_height + 0.45 + 0.18 * smoothstep(u / 0.25)
        roof = Hh
        z = bonnet + (roof - bonnet) * smoothstep((u - 0.25) / 0.17)  # windscreen
        z = z - (roof - (ride_height + 0.72)) * smoothstep((u - 0.62) / 0.25)  # rear window
        z = z - 0.08 * smoothstep((u - 0.93) / 0.07)
        nose = 1 - smoothstep(u / 0.035)
        return z - nose * 0.25 * (z - z_bottom(x))

    def half_width(x):
        u = x / L
        hw = W / 2 * (0.82 + 0.18 * smoothstep(u / 0.15)) * (1 - 0.06 * smoothstep((u - 0.85) / 0.15))
        end = min(1.0, math.sqrt(max(u, 0) / 0.02)) * min(1.0, math.sqrt(max(1 - u, 0) / 0.01))
        return max(hw * (0.35 + 0.65 * end), 1e-3)

    th = np.linspace(0, 2 * math.pi, n_theta, endpoint=False)
    rings = []
    for x in xs[1:-1]:
        zb, zt, hw = z_bottom(x), z_top(x), half_width(x)
        zc, hh = 0.5 * (zt + zb), 0.5 * (zt - zb)
        nexp = 6.0
        cy = np.sign(np.cos(th)) * np.abs(np.cos(th)) ** (2 / nexp)
        sz = np.sign(np.sin(th)) * np.abs(np.sin(th)) ** (2 / nexp)
        taper = 1 - 0.18 * np.clip(sz, 0, 1) ** 3 * smoothstep((zt - ride_height - 0.6) / 0.3)  # tumblehome
        rings.append(np.column_stack([np.full(n_theta, x), hw * cy * taper, zc + hh * sz]))
    pts, faces = _loft(np.array(rings))
    # pull caps onto the nose/tail tip
    pts[-2] = [0.0, 0.0, 0.5 * (z_top(xs[1]) + z_bottom(xs[1]))]
    pts[-1] = [L, 0.0, 0.5 * (z_top(xs[-2]) + z_bottom(xs[-2]))]
    parts = [_surface(pts, faces, "body")]
    fx = front_overhang
    rx = front_overhang + wheelbase
    for pos, x, y in (("FL", fx, -track / 2), ("FR", fx, track / 2), ("RL", rx, -track / 2), ("RR", rx, track / 2)):
        zc = wheel_radius - wheel_sinkage
        parts.append(cylinder(wheel_radius, (x, y - wheel_width / 2, zc), (x, y + wheel_width / 2, zc), n=72, name=f"wheel_{pos}"))
    if rear_wing:
        chord = 0.28
        le = (L - 0.38, 0.0, Hh - 0.12)
        parts.append(wing(chord, width * 0.9, le, angle_deg=wing_angle_deg, name="wing"))
    return TriSurface.merge(parts)


GENERIC_CAR_WHEELS = {
    # defaults of generic_car(): positions (x, y), radius
    "FL": (0.85, -0.775), "FR": (0.85, 0.775), "RL": (3.55, -0.775), "RR": (3.55, 0.775),
}
