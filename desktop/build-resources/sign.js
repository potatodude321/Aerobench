// electron-builder signing hook (build.win.signtoolOptions.sign).
//
// electron-builder signs every .exe it packages, including extraResources. The bundled
// third-party programs (OpenFOAM, ParaView, the MS-MPI installer, the embedded CPython) must
// keep their vendors' own signatures — re-signing would replace e.g. Microsoft's signature on
// msmpisetup.exe with ours. Only AeroBench's own executables are signed.
const THIRD_PARTY = /[\\/]resources[\\/](openfoam|ParaView-[^\\/]+|redist|python)[\\/]/i;

exports.default = async function sign(configuration, packager) {
  if (THIRD_PARTY.test(configuration.path)) return; // keep the vendor's signature
  if (!configuration.cscInfo) return; // no certificate configured (development build)
  const manager = await packager.signingManager.value;
  await manager.doSign(configuration, packager);
};
