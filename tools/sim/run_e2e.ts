// End-to-end simulation harness: compiles and runs the REAL engine source
// files (office-scripts/*.ts) against a mock ExcelScript.Workbook seeded
// from real production data, chaining engines in sequence exactly as a
// manager would run them in Excel. This is the closest verification
// possible without live Excel - it exercises the actual deployed code,
// not a reimplementation.
//
// Usage: NODE_PATH=$(npm root -g) npx ts-node tools/sim/run_e2e.ts <state.json>
import * as fs from "fs";
import * as path from "path";
import * as ts from "typescript";
import { MockWorkbook, Row } from "./mockWorkbook";

function reviveDates(value: any): any {
  if (Array.isArray(value)) {
    return value.map(reviveDates);
  }
  if (value && typeof value === "object" && "__date__" in value) {
    return new Date(value.__date__);
  }
  return value;
}

function loadSeed(jsonPath: string): { [sheet: string]: Row[] } {
  const raw = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
  let out: { [sheet: string]: Row[] } = {};
  for (const sheet of Object.keys(raw)) {
    out[sheet] = reviveDates(raw[sheet]);
  }
  return out;
}

function runEngine(scriptPath: string, workbook: MockWorkbook, log: string[]): void {
  const source = fs.readFileSync(scriptPath, "utf-8");
  const transpiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2019, module: ts.ModuleKind.None },
  });
  const wrapped = transpiled.outputText + "\nmain(workbook);\n";
  // Minimal ExcelScript global: the type itself compiles away, but
  // ExcelScript.ClearApplyTo.contents is a real runtime enum reference in
  // Office Scripts (host-provided global), so the mock needs to supply it.
  const excelScriptGlobal = { ClearApplyTo: { contents: "contents", all: "all", formats: "formats" } };
  const fn = new Function("workbook", "console", "ExcelScript", wrapped);
  const capturedLogs: string[] = [];
  const fakeConsole = {
    log: (...args: any[]) => {
      const line = args.join(" ");
      capturedLogs.push(line);
    },
  };
  fn(workbook, fakeConsole, excelScriptGlobal);
  for (const line of capturedLogs) {
    log.push(`[${path.basename(scriptPath)}] ${line}`);
  }
}

function main() {
  const stateFile = process.argv[2];
  if (!stateFile) {
    console.error("Usage: run_e2e.ts <state.json>");
    process.exit(1);
  }
  const seed = loadSeed(stateFile);
  const workbook = new MockWorkbook(seed);
  const log: string[] = [];
  const engineDir = path.join(__dirname, "..", "..", "office-scripts");

  const defaultPipeline = [
    "ImportEngine.ts",
    "PlanningEngine.ts",
    "PublishEngine.ts",
    "StartTrackingEngine.ts",
    "ComplianceEngine.ts",
    "AdvisorEngine.ts",
    "PerformanceEngine.ts",
    "ReportingEngine.ts",
  ];
  const pipeline = process.argv[3] ? process.argv[3].split(",") : defaultPipeline;

  for (const engine of pipeline) {
    console.log(`\n=== Running ${engine} ===`);
    try {
      runEngine(path.join(engineDir, engine), workbook, log);
      for (const line of log.slice(-20)) {
        if (line.startsWith(`[${engine}]`)) {
          console.log(line);
        }
      }
    } catch (e) {
      console.error(`FAILED at ${engine}:`, (e as Error).message);
      console.error((e as Error).stack);
      process.exit(1);
    }
  }

  const finalState = workbook.dump();
  const outPath = process.argv[4] || path.join(__dirname, "final_state.json");
  fs.writeFileSync(outPath, JSON.stringify(finalState));
  console.log(`\nFinal state written to ${outPath}`);
  console.log("\n--- Row counts per sheet ---");
  for (const sheet of Object.keys(finalState)) {
    console.log(`  ${sheet}: ${finalState[sheet].length} rows`);
  }
}

main();
