/**
 * Launch window: shown the moment the app starts, while the Python engine boots and the
 * main window loads (a few seconds on a cold start). Self-contained HTML (no network, no
 * files) so it can appear before anything else is ready.
 */
import { SPLASH_FONT_TEXT, SPLASH_FONT_TITLE } from "./splashFonts";

export function splashHtml(version: string): string {
  return `<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:">
<style>
  @font-face { font-family: "AB Title"; font-weight: 600; src: url(data:font/woff2;base64,${SPLASH_FONT_TITLE}) format("woff2"); }
  @font-face { font-family: "AB Text"; font-weight: 400; src: url(data:font/woff2;base64,${SPLASH_FONT_TEXT}) format("woff2"); }
  :root { --bg:#15181d; --line:#2d333c; --text:#e3e6ea; --text2:#a1a9b4; --text3:#6c7580; --accent:#8db7e0; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--text);
    font-family:"AB Text","Segoe UI Variable Text","Segoe UI",system-ui,sans-serif; -webkit-user-select:none; user-select:none; overflow:hidden; }
  body { display:flex; flex-direction:column; box-sizing:border-box; padding:34px 36px 26px; border:1px solid var(--line); }
  h1 { margin:0; font-family:"AB Title","Segoe UI",sans-serif; font-weight:600; font-size:34px; letter-spacing:.2px; line-height:1; }
  p.tag { margin:8px 0 0; color:var(--text2); font-size:14.5px; }
  svg { margin:26px -36px 0; width:calc(100% + 72px); height:124px; }
  .body { fill:none; stroke:var(--text3); stroke-width:1.5; }
  .ground { stroke:var(--line); stroke-width:1; }
  .flow { fill:none; stroke:var(--accent); stroke-width:1.4; stroke-linecap:round; stroke-dasharray:60 580; opacity:.85;
          animation: flow 2.4s linear infinite; }
  .flow:nth-of-type(1) { animation-delay:-1.8s; opacity:.3 } .flow:nth-of-type(2) { animation-delay:-1.2s; opacity:.45 }
  .flow:nth-of-type(3) { animation-delay:-.6s; opacity:.65 }
  @keyframes flow { from { stroke-dashoffset: 640 } to { stroke-dashoffset: 0 } }
  @media (prefers-reduced-motion: reduce) { .flow { animation:none; stroke-dasharray:none; opacity:.35 } }
  .foot { margin-top:auto; display:flex; justify-content:space-between; align-items:baseline; font-size:13px; }
  #status { color:var(--text2); } #status.err { color:#e07070; white-space:pre-wrap; }
  .ver { color:var(--text3); font-variant-numeric:tabular-nums; }
</style></head>
<body>
  <h1>AeroBench</h1>
  <p class="tag">Vehicle external aerodynamics</p>
  <svg viewBox="0 0 600 124" aria-hidden="true">
    <path class="flow" d="M0,22 C160,22 240,12 300,10 C380,8 460,16 600,20"/>
    <path class="flow" d="M0,40 C150,40 230,26 300,20 C360,16 430,30 600,38"/>
    <path class="flow" d="M0,58 C110,58 180,46 260,36 C290,30 330,26 360,28 C400,32 470,52 600,56"/>
    <path class="flow" d="M0,76 C90,76 130,70 175,66 C230,60 262,40 312,36 C360,32 392,48 440,58 C480,64 540,72 600,74"/>
    <path class="body" d="M140,98 C142,88 152,80 175,78 L240,72 C262,58 284,48 312,46 C344,44 370,48 394,62 L440,68 C452,70 456,80 456,98 Z"/>
    <circle class="body" cx="192" cy="100" r="13"/>
    <circle class="body" cx="408" cy="100" r="13"/>
    <line class="ground" x1="0" y1="113.5" x2="600" y2="113.5"/>
  </svg>
  <div class="foot"><span id="status">Starting…</span><span class="ver">${version}</span></div>
  <script>
    window.setStatus = function (text, isError) {
      var el = document.getElementById("status");
      el.textContent = text; el.className = isError ? "err" : "";
    };
  </script>
</body></html>`;
}
