/** What the application says about itself: licence, where its source lives, required notices. */

export const REPO_URL = "https://github.com/potatodude321/Aerobench";
export const LICENCE_NAME = "GNU General Public License v3.0 or later";
export const COPYRIGHT = "Copyright © 2026 Abdul Mahmood and the AeroBench contributors.";

/** Required by the OpenCFD trade mark policy wherever AeroBench is described (point 3). */
export const OPENFOAM_DISCLAIMER =
  "This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of the OpenFOAM software via www.openfoam.com, and owner of the OPENFOAM® and OpenCFD® trade marks.";
/** Required acknowledgement of the trade mark (point 4). */
export const OPENFOAM_ACKNOWLEDGEMENT =
  "OPENFOAM® is a registered trade mark of OpenCFD Limited, producer and distributor of the OpenFOAM software via www.openfoam.com.";

export const BUNDLED = [
  ["OpenFOAM v2606", "GPL-3.0-or-later"],
  ["CalculiX CrunchiX 2.23", "GPL-2.0-or-later"],
  ["ParaView 6.2", "BSD-3-Clause and the licences of its libraries"],
  ["Microsoft MPI 10.1.3", "MIT"],
] as const;
