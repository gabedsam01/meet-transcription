import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const tests = ["config.test.mjs", "api.test.mjs"].map((name) => join(__dirname, name));
const result = spawnSync(process.execPath, ["--test", ...tests], {
  stdio: "inherit",
});

process.exit(result.status ?? 1);
