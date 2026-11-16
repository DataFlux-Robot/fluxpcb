// Route one SimpleRouteJson with tscircuit's capacity autorouter or with
// KiCad Routing Tools (WASM), under a wall-clock cap, and write the traces.
//
//   node harness/srj_route.mjs <tscircuit|krt> <in.json> <out.json> <cap_s>
//
// out.json: { tool, version, solved, failed, timedOut, error, elapsedMs, steps, traces }
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { Worker } from 'node:worker_threads';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const [tool, inPath, outPath, capArg] = process.argv.slice(2);
const capMs = Number(capArg ?? 1200) * 1000;
const srj = JSON.parse(fs.readFileSync(inPath, 'utf-8'));

// KiCad Routing Tools: a 0.05 mm grid (at the README's 0.1 mm the grid cannot leave a
// 0.4 mm-pitch pad at the board clearance) and a per-segment search cap scaled to it.
// The clearance is the board's Default net class clearance (argument 5).
const KRT_GRID_STEP_MM = 0.05;
const KRT_MAX_ITERATIONS = 2_000_000;

function version(pkg) {
  try {
    return require(`${pkg}/package.json`).version;
  } catch {
    return 'unknown';
  }
}

function tscircuit() {
  const lib = require('@tscircuit/capacity-autorouter');
  const solver = new lib.AutoroutingPipelineSolver(srj);
  const t0 = performance.now();
  let steps = 0;
  let timedOut = false;
  let error = null;
  try {
    while (!solver.solved && !solver.failed) {
      if (performance.now() - t0 > capMs) {
        timedOut = true;
        break;
      }
      solver.step();
      steps++;
    }
  } catch (e) {
    error = String(e?.message ?? e);
  }
  let traces = [];
  try {
    traces = solver.getOutputSimpleRouteJson().traces ?? [];
  } catch {
    traces = [];
  }
  return { tool, version: version('@tscircuit/capacity-autorouter'), solved: !!solver.solved, failed: !!solver.failed, timedOut, error: error ?? solver.error ?? null, elapsedMs: performance.now() - t0, steps, traces };
}

/** The whole-board solve in a worker, so the cap can stop it. */
function krtWhole(opts) {
  const pkg = pathToFileURL(require.resolve('@tscircuit/krt-wasm')).href;
  const code = `
    const { parentPort, workerData } = require('node:worker_threads');
    (async () => {
      const krt = await import(workerData.pkg);
      await krt.initKiCadRoutingToolsAutorouter();
      try {
        parentPort.postMessage({ traces: new krt.KiCadRoutingToolsAutorouter(workerData.srj, workerData.opts).solveSync() });
      } catch (e) {
        parentPort.postMessage({ error: String((e && e.message) || e) });
      }
    })().catch((e) => parentPort.postMessage({ error: String((e && e.message) || e) }));`;
  return new Promise((resolve) => {
    const w = new Worker(code, { eval: true, workerData: { pkg, srj, opts } });
    const timer = setTimeout(() => {
      void w.terminate();
      resolve({ timedOut: true });
    }, capMs);
    w.on('message', (m) => {
      clearTimeout(timer);
      void w.terminate();
      resolve(m);
    });
    w.on('error', (e) => {
      clearTimeout(timer);
      resolve({ error: String(e?.message ?? e) });
    });
  });
}

async function krt(clearance) {
  const lib = await import('@tscircuit/krt-wasm');
  await lib.initKiCadRoutingToolsAutorouter();
  const opts = { gridStep: KRT_GRID_STEP_MM, clearance, maxIterations: KRT_MAX_ITERATIONS };
  const t0 = performance.now();
  const base = { tool, version: version('@tscircuit/krt-wasm'), failed: false, steps: 1 };
  const whole = await krtWhole(opts);
  if (whole.traces) return { ...base, solved: true, timedOut: false, error: null, elapsedMs: performance.now() - t0, traces: whole.traces };
  if (whole.timedOut) return { ...base, solved: false, timedOut: true, error: 'whole-board solve did not finish inside the cap', elapsedMs: performance.now() - t0, traces: [] };
  // The package routes all or nothing. After a throw inside the cap: one connection at a
  // time, in the board's order, routed copper as obstacles, skipping what it cannot close.
  const layers = ['top', ...Array.from({ length: Math.max(0, srj.layerCount - 2) }, (_, i) => `inner${i + 1}`), 'bottom'];
  const viaMm = srj.minViaDiameter ?? 0.6;
  const obstacles = [...srj.obstacles];
  const traces = [];
  let skipped = 0;
  let timedOut = false;
  for (const c of srj.connections) {
    if (performance.now() - t0 > capMs) {
      timedOut = true;
      break;
    }
    let got;
    try {
      got = new lib.KiCadRoutingToolsAutorouter({ ...srj, connections: [c], obstacles }, opts).solveSync();
    } catch {
      skipped++;
      continue;
    }
    for (const tr of got) {
      traces.push({ ...tr, connection_name: tr.connection_name ?? c.name });
      let prev = null;
      for (const st of tr.route) {
        if (st.route_type === 'via') {
          obstacles.push({ type: 'rect', layers, center: { x: st.x, y: st.y }, width: viaMm, height: viaMm, connectedTo: [c.name] });
          prev = null;
          continue;
        }
        const layer = st.layer ?? 'top';
        const w = st.width ?? srj.minTraceWidth;
        if (prev && prev.layer === layer) {
          obstacles.push({
            type: 'rect', layers: [layer], center: { x: (prev.x + st.x) / 2, y: (prev.y + st.y) / 2 },
            width: Math.abs(st.x - prev.x) + w, height: Math.abs(st.y - prev.y) + w, connectedTo: [c.name],
          });
        }
        prev = { x: st.x, y: st.y, layer };
      }
    }
  }
  const first = (whole.error ?? '').split(';')[0].slice(0, 160);
  return { ...base, solved: true, timedOut, error: `whole-board solve: ${first}; routed connection by connection, ${skipped} of ${srj.connections.length} skipped`, elapsedMs: performance.now() - t0, traces, netByNet: true };
}

const result = tool === 'krt' ? await krt(Number(process.argv[6] ?? srj.minTraceWidth)) : tscircuit();
fs.writeFileSync(outPath, JSON.stringify(result));
console.log(JSON.stringify({ ...result, traces: result.traces.length }));
process.exit(0);
