// Renders OpenSCAD jobs with the WebAssembly build of OpenSCAD.
// stdin: {"designDir": "...", "jobs": [{"main": "x.scad" | null, "code": "..." | null, "out": "/abs/out.stl", "defines": {"k": 1}}]}
// stdout: one JSON line per job: {"out": ..., "ok": bool, "ms": n, "log": "..."}
import { createOpenSCAD } from "openscad-wasm-prebuilt";
import fs from "node:fs";
import path from "node:path";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const designFiles = fs.readdirSync(input.designDir)
  .filter((f) => /\.(scad|stl)$/i.test(f))
  .map((f) => [f, fs.readFileSync(path.join(input.designDir, f))]);

function defineArg(key, value) {
  const v = typeof value === "string" ? JSON.stringify(value) : String(value);
  return ["-D", `${key}=${v}`];
}

for (const job of input.jobs) {
  const log = [];
  const t0 = Date.now();
  let ok = false;
  try {
    const os = await createOpenSCAD({ noInitialRun: true, print: (t) => log.push(t), printErr: (t) => log.push(t) });
    const inst = os.getInstance();
    inst.FS.mkdir("/d");
    for (const [name, data] of designFiles) inst.FS.writeFile("/d/" + name, data);
    let main = job.main;
    if (job.code) {
      main = "_job.scad";
      inst.FS.writeFile("/d/" + main, job.code);
    }
    const args = ["/d/" + main, "-o", "/out.stl", "--backend=manifold", "--export-format=binstl"];
    for (const [k, v] of Object.entries(job.defines || {})) args.push(...defineArg(k, v));
    const code = inst.callMain(args);
    const data = inst.FS.readFile("/out.stl");
    if (code === 0 && data.length > 84) {
      fs.writeFileSync(job.out, data);
      ok = true;
    }
  } catch (e) {
    log.push(String(e));
  }
  process.stdout.write(JSON.stringify({ out: job.out, ok, ms: Date.now() - t0, log: log.slice(-15).join("\n") }) + "\n");
}
