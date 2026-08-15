#!/usr/bin/env node
"use strict";

const { spawnSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { login, loadSession, clearSession, hasCompletedNewVersionLogin } = require("./auth");
const DEFAULT_OLLAMA_MODELS = ["llama3.2:1b", "qwen3-embedding:4b"]

const packageRoot = path.resolve(__dirname, "..");
const pythonAgent = path.join(packageRoot, "agent_rag.py");
const requirementsFile = path.join(packageRoot, "requirements.txt");



function loadEnvFile() {
  const envFile = path.join(packageRoot, ".env");
  if (!fs.existsSync(envFile)) return;
  for (const line of fs.readFileSync(envFile, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const [key, ...rest] = trimmed.split("=");
    if (key && rest.length && !process.env[key.trim()]) {
      process.env[key.trim()] = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
    }
  }
}

loadEnvFile();

const HELP = `
╔══════════════════════════════════════════════════════════════════════════════╗
║                          CLAW CODER - Autonomous local AI agent              ║
╚══════════════════════════════════════════════════════════════════════════════╝

📖 USAGE:
  claw-coder <command> [options]

╔══════════════════════════════════════════════════════════════════════════════╗
║ 💬 CHAT & INTERACTION                                                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  claw-coder [--pdf <file>...]    Start interactive chat (optionally preload  ║
║                                  PDFs)                                        ║
║  claw-coder --ui textual         Use improved UI with scrolling & selection ║
║  models                           List local Ollama models                    ║
║  <model-name>                     Start claw-coder with specific Ollama model   ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 📚 KNOWLEDGE BASE                                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  ingest <paths...>              Ingest files/directories into graph + vector ║
║                                RAG                                           ║
║  ingest-code <file>             Ingest one source file                       ║
║  ingest-pdf <file>              Ingest a PDF or text document (.pdf, .txt,   ║
║                                .md)                                          ║
║  search <query>                 Search vector RAG with graph reranking       ║
║  graph <query>                  Search the knowledge graph only              ║
║  summary                        Show graph node/edge counts                  ║
║  languages                      Show Tree-sitter language support            ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ ⚙️  SETUP & CONFIGURATION                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  setup                        Install Python dependencies for Claw Coder     ║
║  doctor                       Check local Node/Python/Ollama setup           ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 💳 ACCOUNT & BILLING                                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  login [provider]              Log in via OAuth (default: github)            ║
║  logout                        Clear saved session                           ║
║  whoami                        Show current logged-in user                   ║
║  clear-storage                 Clear all Claw-Coder local storage           ║
║  usage                        Show this month's cloud tool usage             ║
║  credits                      Show paid credit balance                       ║
║  upgrade-plan                 Subscribe to available plans                   ║
║  topup                        Buy extra pay-as-you-go credits                ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 🔧 COMMON OPTIONS                                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  --top-k <n>                    Number of results to return                  ║
║  --depth <n>                    Graph traversal depth for graph search       ║
║  --graph <file>                 Knowledge graph JSON path                    ║
║  --db <dir>                     ChromaDB directory                           ║
║  --collection <name>            ChromaDB collection                          ║
║  --model <name>                 Ollama model for claw-coder                    ║
║  --embedding-model <name>       Ollama embedding model                       ║
║  --ui <rich|textual>            Choose UI style (default: rich)              ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║ 📝 EXAMPLES                                                                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  claw-coder setup               Install dependencies                          ║
║  claw-coder doctor              Check system setup                            ║
║  claw-coder ingest .            Ingest current directory                      ║
║  claw-coder graph "imports tree" --depth 2    Search knowledge graph          ║ 
║  claw-coder search "reranking" --top-k 5       Search with context            ║
║  claw-coder                    Start interactive chat                       ║
║  claw-coder --pdf report.pdf   Chat with PDF context                         ║
║  claw-coder --ui textual       Use improved UI with scrolling & selection     ║
║  claw-coder qwen2.5-coder:7b   Use specific model                            ║
╚══════════════════════════════════════════════════════════════════════════════╝

🌐 Visit https://github.com/gabriel-c70/Claw-Coder.git for more information
`;

function printHelp() {
  console.log(HELP.trimStart());
}

function run(command, args, options = {}) {
  const timeout = options.timeout || 0;
  const result = spawnSync(command, args, {
    cwd: options.cwd || process.cwd(),
    stdio: options.stdio || "inherit",
    env: options.env || process.env,
    encoding: "utf8",
    timeout: timeout,
  });

  if (result.error) {
    return { status: 1, error: result.error.message, stdout: result.stdout || "", stderr: result.stderr || "" };
  }
  return {
    status: typeof result.status === "number" ? result.status : 1,
    stdout: result.stdout || "",
    stderr: result.stderr || "",
  };
}

function commandExists(command, args = ["--version"]) {
  const timeout = isCodespaces() ? 30000 : 10000; // 30s for Codespaces, 10s otherwise
  const result = run(command, args, { stdio: "pipe", timeout: timeout });
  return result.status === 0;
}

function getPythonVersion(pythonCommand) {
  const result = run(pythonCommand, ["--version"], { stdio: "pipe" });
  if (result.status !== 0) return null;
  
  const versionMatch = (result.stdout || result.stderr || "").match(/Python (\d+)\.(\d+)/);
  if (!versionMatch) return null;
  
  return {
    major: parseInt(versionMatch[1]),
    minor: parseInt(versionMatch[2]),
    full: `${versionMatch[1]}.${versionMatch[2]}`
  };
}

function isPythonVersionCompatible(pythonCommand) {
  const version = getPythonVersion(pythonCommand);
  if (!version) return false;
  
  // Python 3.8+ should work, but we prefer 3.11 or 3.12
  // We'll return true for all modern Python versions and handle issues gracefully
  return version.major === 3 && version.minor >= 8;
}

function getPythonVersionPreference(pythonCommand) {
  const version = getPythonVersion(pythonCommand);
  if (!version) return { preference: 'unknown', compatible: false };
  
  if (version.major === 3 && version.minor === 12) return { preference: 'preferred', compatible: true };
  if (version.major === 3 && version.minor === 11) return { preference: 'preferred', compatible: true };
  if (version.major === 3 && version.minor === 10) return { preference: 'good', compatible: true };
  if (version.major === 3 && version.minor === 9) return { preference: 'acceptable', compatible: true };
  if (version.major === 3 && version.minor === 8) return { preference: 'acceptable', compatible: true };
  if (version.major === 3 && version.minor >= 13) return { preference: 'experimental', compatible: true };
  
  return { preference: 'unsupported', compatible: false };
}

function findPython() {
  if (process.env.CLAW_PYTHON) {
    return process.env.CLAW_PYTHON;
  }

  // Always prioritize Claw-Coder's own virtual environment
  const venvCandidates = process.platform === "win32"
    ? [
        path.join(packageRoot, "venv", "Scripts", "python.exe"),
        path.join(packageRoot, ".venv", "Scripts", "python.exe"),
      ]
    : [
        path.join(packageRoot, "venv", "bin", "python3"),
        path.join(packageRoot, "venv", "bin", "python"),
        path.join(packageRoot, ".venv", "bin", "python3"),
        path.join(packageRoot, ".venv", "bin", "python"),
      ];

  // First check if Claw-Coder venv exists and use it
  for (const candidate of venvCandidates) {
    if (fs.existsSync(candidate)) {
      const versionInfo = getPythonVersionPreference(candidate);
      const version = getPythonVersion(candidate);
      
      if (versionInfo.compatible) {
        if (versionInfo.preference === 'preferred') {
          return candidate; // No warning for preferred versions
        } else if (versionInfo.preference === 'experimental') {
          console.warn(`Note: Using Python ${version?.full || 'unknown'} (experimental support)`);
          console.warn(`  Some features may not work perfectly. Consider using Python 3.11 or 3.12 for best compatibility.`);
        } else if (versionInfo.preference === 'acceptable') {
          console.warn(`Note: Using Python ${version?.full || 'unknown'} (acceptable support)`);
          console.warn(`  Python 3.11 or 3.12 recommended for best performance.`);
        }
        return candidate;
      } else {
        console.warn(`Warning: Claw-Coder venv uses Python ${version?.full || 'unknown'} (unsupported)`);
        console.warn(`  Python 3.8+ required. Consider recreating the venv with a newer Python version.`);
        return candidate; // Still try to use it
      }
    }
  }

  // If no Claw-Coder venv, look for system Python with compatible version
  // Only warn if we're in the Claw-Coder directory (users can run claw from anywhere)
  if (process.cwd() === packageRoot) {
    console.warn(`Note: No Claw-Coder virtual environment found.`);
    console.warn(`  It's recommended to run 'claw-coder setup' to create a proper isolated environment.`);
  }


  // If no Claw-Coder venv, look for system Python with compatible version
  const systemCandidates = ["python3.12", "python3.11", "python3.10", "python3.9", "python3.8", "python3", "python"];
  for (const candidate of systemCandidates) {
    if (commandExists(candidate)) {
      const versionInfo = getPythonVersionPreference(candidate);
      if (versionInfo.compatible) {
        const version = getPythonVersion(candidate);
        if (versionInfo.preference !== 'preferred') {
          console.warn(`Note: Using system Python ${version?.full || 'unknown'} (${versionInfo.preference} support)`);
        }
        return candidate;
      }
    }
  }

  // Fallback to any available Python if no compatible version found
  if (commandExists("python3")) {
    return "python3";
  }
  if (commandExists("python")) {
    return "python";
  }
  return null;
}

const crypto = require("node:crypto");

function getDeviceId() {
  const idFile = path.join(os.homedir(), ".claw-coder", "device_id");
  try {
    return fs.readFileSync(idFile, "utf8").trim();
  } catch {
    const id = crypto.randomUUID();
    fs.mkdirSync(path.dirname(idFile), { recursive: true });
    fs.writeFileSync(idFile, id, "utf8");
    return id;
  }
}

function getTelemetryConsentFile() {
  return path.join(os.homedir(), ".claw-coder", "telemetry_consent.json");
}

function getTelemetryConsent() {
  try {
    const data = JSON.parse(fs.readFileSync(getTelemetryConsentFile(), "utf8"));
    return data.consent === true;
  } catch {
    return null; // never asked yet
  }
}

function askTelemetryConsent() {
  console.log("\nClaw-Coder can send anonymous usage pings — device ID, command run,");
  console.log("CLI version, platform. No code, no file contents, no personal info.");
  console.log("This helps us know what people actually use and improve that.\n");

  const readline = require("node:readline");
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question("Enable anonymous usage telemetry? [y/N] ", (answer) => {
      rl.close();
      const consent = answer.trim().toLowerCase() === "y";
      fs.mkdirSync(path.dirname(getTelemetryConsentFile()), { recursive: true });
      fs.writeFileSync(getTelemetryConsentFile(), JSON.stringify({ consent, decidedAt: Date.now() }), "utf8");
      resolve(consent);
    });
  });
}

async function pingTelemetry(command) {
  let consent = getTelemetryConsent();
  if (consent === null) {
    consent = await askTelemetryConsent();
  }
  if (!consent) return;

  if (process.env.CLAW_TELEMETRY === "0") return;  // opt-out, see note below

  const deviceId = getDeviceId();
  const pkg = require(path.join(packageRoot, "package.json"));

  fetch("https://nqbrdafvdfntxvhbyama.supabase.co/rest/v1/rpc/record_device_activity", {
    method: "POST",
    headers: {
      "apikey": "sb_publishable_fKGO3iZ6nCEtPUqPsQb_nQ_jIXwMtCJ",
      "Authorization": "Bearer sb_publishable_fKGO3iZ6nCEtPUqPsQb_nQ_jIXwMtCJ",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      p_device_id: deviceId,
      p_command: command,
      p_version: pkg.version,
      p_platform: process.platform,
    }),
    signal: AbortSignal.timeout(3000),
  }).catch(() => {
  });  // fire-and-forget — never let this break or slow the CLI
}

function readOption(args, names, fallback = null) {
  for (let index = 0; index < args.length; index += 1) {
    if (names.includes(args[index])) {
      return args[index + 1] || fallback;
    }
    for (const name of names) {
      if (args[index].startsWith(`${name}=`)) {
        return args[index].slice(name.length + 1);
      }
    }
  }
  return fallback;
}

function collectDocumentOptions(args) {
  const output = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--pdf" || arg === "--document") {
      const value = args[index + 1];
      if (value) {
        output.push("--pdf", value);
        index += 1;
      }
      continue;
    }
    if (arg.startsWith("--pdf=") || arg.startsWith("--document=")) {
      output.push("--pdf", arg.split("=").slice(1).join("="));
    }
  }
  return output;
}

function collectGlobalOptions(args) {
  const output = [];
  const mappings = [
    [["--model"], "--model"],
    [["--embedding-model"], "--embedding-model"],
    [["--db", "--db-path"], "--db-path"],
    [["--collection"], "--collection"],
    [["--graph", "--knowledge-graph-path"], "--knowledge-graph-path"],
    [["--workspace-mode"], "--workspace-mode"],
    [["--workspace-ssh"], "--workspace-ssh"],
    [["--workspace-remote-dir"], "--workspace-remote-dir"],
  ];

  for (const [aliases, target] of mappings) {
    const value = readOption(args, aliases);
    if (value) {
      output.push(target, value);
    }
  }
  return output;
}

function stripKnownOptions(args) {
  const optionsWithValues = new Set([
    "--model",
    "--embedding-model",
    "--db",
    "--db-path",
    "--collection",
    "--graph",
    "--knowledge-graph-path",
    "--workspace-mode",
    "--workspace-ssh",
    "--workspace-remote-dir",
    "--top-k",
    "--depth",
    "--language",
    "--pdf",
    "--document",
    "--ui",
  ]);
  const flags = new Set(["--no-recursive", "--no-vector-rag", "--no-hybrid-rerank"]);
  const cleaned = [];

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    const equalsName = arg.includes("=") ? arg.split("=")[0] : null;
    if (optionsWithValues.has(arg)) {
      index += 1;
      continue;
    }
    if (equalsName && optionsWithValues.has(equalsName)) {
      continue;
    }
    if (flags.has(arg)) {
      continue;
    }
    cleaned.push(arg);
  }

  return cleaned;
}

function checkPythonDependencies(python) {
  const requiredPackages = ['ollama', 'chromadb', 'ddgs', 'pypdf', 'tree_sitter', 'rich'];
  const importCheck = run(
    python,
    [
      "-c",
      `import importlib.util as u; pkgs=${JSON.stringify(requiredPackages)}; missing=[p for p in pkgs if u.find_spec(p) is None]; print(','.join(missing) if missing else '')`,
    ],
    { cwd: packageRoot, stdio: "pipe" },
  );
  
  if (importCheck.status !== 0) {
    return null; // Error checking dependencies
  }
  
  const missing = (importCheck.stdout || "").trim();
  if (missing) {
    return missing.split(',').filter(p => p);
  }
  
  return []; // All dependencies installed
}

function ensureDependencies(python) {
  const missing = checkPythonDependencies(python);
  
  if (missing === null) {
    console.error("Error checking Python dependencies. Please run `claw-coder setup` manually.");
    return false;
  }
  
  if (missing.length === 0) {
    return true; // All dependencies present
  }
  
  // Check Python version and provide helpful information
  const pythonVersion = getPythonVersion(python);
  const versionInfo = getPythonVersionPreference(python);
  
  console.log(`Missing Python dependencies: ${missing.join(', ')}`);
  console.log("Installing missing dependencies automatically...");
  console.log(`Using Python: ${python} (${pythonVersion?.full || 'unknown'})`);
  
  if (versionInfo.preference === 'experimental') {
    console.warn(`Note: Python ${pythonVersion?.full} is experimental - some dependencies may have compatibility issues.`);
  } else if (versionInfo.preference === 'acceptable') {
    console.warn(`Note: Python ${pythonVersion?.full} has acceptable support - Python 3.11/3.12 recommended.`);
  } else if (!versionInfo.compatible) {
    console.warn(`Warning: Python ${pythonVersion?.full} may not be compatible with all dependencies.`);
    console.warn(`  Python 3.8+ recommended. Attempting installation anyway...`);
  }
  
  if (!fs.existsSync(requirementsFile)) {
    console.error(`Missing requirements file: ${requirementsFile}`);
    console.error("Please run `claw-coder setup` manually.");
    return false;
  }
  
  // Skip pip upgrade if it fails - it's not critical
  const upgradePip = run(python, ["-m", "pip", "install", "--upgrade", "pip"], { cwd: packageRoot, stdio: "pipe" });
  if (upgradePip.status !== 0) {
    // Check if pip itself is broken (common with Python 3.13)
    const errorOutput = (upgradePip.stderr || upgradePip.stdout || "").toLowerCase();
    if (errorOutput.includes("importerror") || errorOutput.includes("cannot import")) {
      console.error("Error: pip in this Python environment is broken or incompatible.");
      console.error(`This is common with Python ${pythonVersion?.full || 'unknown'}.`);
      console.error("Solutions:");
      console.error("1. Use a different Python version (3.11 or 3.12 recommended):");
      console.error("   export CLAW_PYTHON=$(which python3.12)");
      console.error("2. Or fix pip in this environment:");
      console.error("   python -m ensurepip --upgrade");
      console.error("3. Or use Claw-Coder's built-in environment by running:");
      console.error("   claw-coder setup");
      return false;
    }
    console.warn("Warning: Failed to upgrade pip (this is usually not critical)");
  }
  
  const result = run(
    python,
    ["-m", "pip", "install", "-r", requirementsFile, "--default-timeout", "120", "--retries", "10"],
    { cwd: packageRoot },
  );
  
  if (result.status !== 0) {
    // Check if the installation failed due to pip issues
    const errorOutput = (result.stderr || result.stdout || "").toLowerCase();
    if (errorOutput.includes("importerror") || errorOutput.includes("cannot import")) {
      console.error("Error: pip in this Python environment is broken or incompatible.");
      console.error(`This is common with Python ${pythonVersion?.full || 'unknown'}.`);
      console.error("Solutions:");
      console.error("1. Use a different Python version (3.11 or 3.12 recommended):");
      console.error("   export CLAW_PYTHON=$(which python3.12)");
      console.error("2. Or fix pip in this environment:");
      console.error("   python -m ensurepip --upgrade");
      console.error("3. Or use Claw-Coder's built-in environment by running:");
      console.error("   claw-coder setup");
      return false;
    }
    console.error("Failed to install Python dependencies automatically.");
    console.error("Please run `claw-coder setup` manually to install dependencies.");
    return false;
  }
  
  console.log("Python dependencies installed successfully.");
  
  // Verify installation
  const stillMissing = checkPythonDependencies(python);
  if (stillMissing && stillMissing.length > 0) {
    console.error(`Some dependencies still missing: ${stillMissing.join(', ')}`);
    console.error("Please run `claw-coder setup` manually.");
    return false;
  }
  
  return true;
}

function runAgent(agentArgs) {
  const python = findPython();
  if (!python) {
    console.error("Python was not found. Install Python 3 or set CLAW_PYTHON=/path/to/python.");
    process.exitCode = 1;
    return;
  }

  // Check and ensure dependencies are installed before running the agent
  if (!ensureDependencies(python)) {
    process.exitCode = 1;
    return;
  }

  // Set environment variables for better ollama stability in resource-constrained environments
  const agentEnv = {
    ...process.env,
    OLLAMA_KEEP_ALIVE: "-1",
    OLLAMA_NUM_LOAD_RETRY: "10",
    OLLAMA_LOAD_TIMEOUT: "10m",
    OLLAMA_MAX_QUEUE: "512",
    OLLAMA_NUM_PARALLEL: "1"
  };

  const result = run(python, [pythonAgent, ...agentArgs], { 
    cwd: process.cwd(),
    env: agentEnv
  });
  process.exitCode = result.status;
}

function bootstrapPythonSpec() {
  if (process.env.CLAW_PYTHON) {
    return { command: process.env.CLAW_PYTHON, prefixArgs: [] };
  }

  const specs = process.platform === "win32"
    ? [
        { command: "py", prefixArgs: ["-3.12"] },
        { command: "py", prefixArgs: ["-3.11"] },
        { command: "py", prefixArgs: ["-3.10"] },
        { command: "py", prefixArgs: ["-3.9"] },
        { command: "py", prefixArgs: ["-3.8"] },
        { command: "python3", prefixArgs: [] },
        { command: "python", prefixArgs: [] },
      ]
    : [
        { command: "python3.12", prefixArgs: [] },
        { command: "python3.11", prefixArgs: [] },
        { command: "python3.10", prefixArgs: [] },
        { command: "python3.9", prefixArgs: [] },
        { command: "python3.8", prefixArgs: [] },
        { command: "python3", prefixArgs: [] },
        { command: "python", prefixArgs: [] },
      ];

  for (const spec of specs) {
    const versionArgs = spec.prefixArgs.length ? [...spec.prefixArgs, "--version"] : ["--version"];
    if (commandExists(spec.command, versionArgs)) {
      // Check if the Python version is compatible
      if (isPythonVersionCompatible(spec.command)) {
        return spec;
      }
    }
  }
  return null;
}

function venvPythonPath(venvDir) {
  return process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python3");
}

function runSetup() {
  let python = findPython();
  if (!python) {
    const bootstrap = bootstrapPythonSpec();
    if (!bootstrap) {
      console.error("Python was not found. Install Python 3.11 or 3.12, or set CLAW_PYTHON=/path/to/python.");
      process.exitCode = 1;
      return;
    }
    python = bootstrap.command;
  }
  if (!fs.existsSync(requirementsFile)) {
    console.error(`Missing requirements file: ${requirementsFile}`);
    process.exitCode = 1;
    return;
  }

  const venvDir = path.join(packageRoot, "venv");
  const usingBundledVenv = python.includes(`${path.sep}venv${path.sep}`)
    || python.includes(`${path.sep}.venv${path.sep}`);

  if (!usingBundledVenv && !process.env.CLAW_PYTHON) {
    const bootstrap = bootstrapPythonSpec();
    if (bootstrap && !fs.existsSync(venvDir)) {
      console.log("Creating local Python virtual environment (prefer Python 3.12 for ChromaDB support)...");
      const createVenv = run(
        bootstrap.command,
        [...bootstrap.prefixArgs, "-m", "venv", venvDir],
        { cwd: packageRoot },
      );
      if (createVenv.status !== 0) {
        process.exitCode = createVenv.status || 1;
        return;
      }
      python = venvPythonPath(venvDir);
    }
  }

  console.log(`Installing Python dependencies with ${python}...`);
  const versionCheck = run(
    python,
    ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
    { cwd: packageRoot, stdio: "pipe" },
  );
  const pyVersion = (versionCheck.stdout || "").trim();
  if (pyVersion === "3.13") {
    console.warn(
      "Warning: Python 3.13 cannot install ChromaDB (vector RAG). "
      + "Remove ./venv and run `claw-coder setup` again so it can create a Python 3.12 environment.",
    );
  }

  const upgradePip = run(python, ["-m", "pip", "install", "--upgrade", "pip"], { cwd: packageRoot });
  if (upgradePip.status !== 0) {
    process.exitCode = upgradePip.status || 1;
    return;
  }

  const result = run(
    python,
    ["-m", "pip", "install", "-r", requirementsFile, "--default-timeout", "120", "--retries", "10"],
    { cwd: packageRoot },
  );
  if (result.status !== 0) {
    console.error("Python dependency install failed. Check your network connection and run `claw-coder setup` again.");
  }
  console.log("");
  installOllama();
  startOllamaServe();
  pullDefaultModels(DEFAULT_OLLAMA_MODELS);
  process.exitCode = 0;

}
function runUpgrade() {
  console.log("Upgrading claw-coder...");
  const result = spawnSync("npm", ["install", "-g", "claw-coder@latest"], { stdio: "inherit" });
  if (result.status === 0) {
    try { fs.unlinkSync(UPDATE_CHECK_FILE); } catch {}  // force a fresh check next run
    console.log("\n✓ Upgraded. Run `claw --version` to confirm.");
  } else {
    console.error("\nUpgrade failed. Try manually: npm install -g claw-coder@latest");
    process.exitCode = result.status || 1;
  }
}

function runDoctor() {
  const python = findPython();
  const checks = [
    ["Node.js", true, process.version],
    ["Python", Boolean(python), python || "not found"],
    ["Ollama", commandExists("ollama"), commandExists("ollama") ? "found" : "not found"],
    ["agent_rag.py", fs.existsSync(pythonAgent), pythonAgent],
    ["requirements.txt", fs.existsSync(requirementsFile), requirementsFile],
  ];

  for (const [name, ok, detail] of checks) {
    console.log(`${ok ? "OK " : "NO "} ${name}: ${detail}`);
  }

  if (python) {
    const importCheck = run(
      python,
      [
        "-c",
        "import importlib.util as u; pkgs=['ollama','chromadb','ddgs','pypdf','tree_sitter','rich']; missing=[p for p in pkgs if u.find_spec(p) is None]; print('OK  Python packages: installed') if not missing else print('NO  Python packages missing: ' + ', '.join(missing))",
      ],
      { cwd: packageRoot, stdio: "pipe" },
    );
    if (importCheck.stdout) {
      process.stdout.write(importCheck.stdout.endsWith("\n") ? importCheck.stdout : `${importCheck.stdout}\n`);
    }
    if (importCheck.stderr) {
      process.stderr.write(importCheck.stderr);
    }
    if (importCheck.status !== 0 && !importCheck.stdout.includes("NO  Python packages missing")) {
      console.log("NO  Python packages: missing; run `claw-coder setup`");
    }
  }
}
function getApiUrl() {
  return process.env.RATE_LIMIT_API_URL || "https://claw-coder-3.onrender.com";
}
async function apiFetch(pathname, session, options = {}) {
  const timeoutMs = Number(process.env.RATE_LIMIT_TIMEOUT_MS || 45000);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${getApiUrl()}${pathname}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      signal: controller.signal,
    });

    if (response.status === 401) {
      clearSession();
      throw new Error("Your session is invalid or expired. Run `claw-coder login` again.");
    }

    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!response.ok) {
      const detail = data.detail || data.error || data;
      const message = typeof detail === "string" ? detail : detail.message || JSON.stringify(detail);
      throw new Error(message);
    }
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

const UPDATE_CHECK_FILE = path.join(os.homedir(), ".claw-coder", "update_check.json");
const UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000; // once a day, not every single run

async function checkForUpdate() {
  let cache = {};
  try {
    cache = JSON.parse(fs.readFileSync(UPDATE_CHECK_FILE, "utf8"));
  } catch {}

  const now = Date.now();
  if (cache.checkedAt && now - cache.checkedAt < UPDATE_CHECK_INTERVAL_MS) {
    return cache.latestVersion || null;   // reuse yesterday's result, don't hit npm every run
  }

  try {
    const res = await fetch("https://registry.npmjs.org/claw-coder/latest", {
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    const latestVersion = data.version;

    fs.mkdirSync(path.dirname(UPDATE_CHECK_FILE), { recursive: true });
    fs.writeFileSync(UPDATE_CHECK_FILE, JSON.stringify({ checkedAt: now, latestVersion }), "utf8");

    return latestVersion;
  } catch {
    return null;   // offline / npm unreachable — fail silently, never block the CLI
  }
}

function isNewerVersion(latest, current) {
  const l = latest.split(".").map(Number);
  const c = current.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if ((l[i] || 0) > (c[i] || 0)) return true;
    if ((l[i] || 0) < (c[i] || 0)) return false;
  }
  return false;
}
function sleepSync(ms) {
  const platform = process.platform;
  if (platform === "win32") {
    spawnSync("powershell", ["-NoProfile", "-Command", `Start-Sleep -Milliseconds ${ms}`], { stdio: "ignore" });
  } else {
    spawnSync("sleep", [(ms / 1000).toString()], { stdio: "ignore" });
  }
}

function isCodespaces() {
  return process.env.GITHUB_CODESPACES === "true" || process.env.CODESPACES === "true";
}

function getOllamaPidFile() {
  const path = require("path");
  const os = require("os");
  return path.join(os.tmpdir(), "ollama.pid");
}

function saveOllamaPid(pid) {
  try {
    const fs = require("fs");
    const path = require("path");
    const os = require("os");
    const pidFile = getOllamaPidFile();
    fs.writeFileSync(pidFile, pid.toString(), "utf8");
  } catch (e) {
    // Ignore errors writing pid file
  }
}

function getOllamaPid() {
  try {
    const fs = require("fs");
    const pidFile = getOllamaPidFile();
    if (fs.existsSync(pidFile)) {
      const pid = parseInt(fs.readFileSync(pidFile, "utf8").trim());
      return pid;
    }
  } catch (e) {
    // Ignore errors reading pid file
  }
  return null;
}

function clearOllamaPid() {
  try {
    const fs = require("fs");
    const pidFile = getOllamaPidFile();
    if (fs.existsSync(pidFile)) {
      fs.unlinkSync(pidFile);
    }
  } catch (e) {
    // Ignore errors clearing pid file
  }
}

function isProcessRunning(pid) {
  try {
    const result = run("kill", ["-0", pid.toString()], { stdio: "pipe" });
    return result.status === 0;
  } catch (e) {
    return false;
  }
}

function isOllamaRunning() {
  // First check if we have a saved PID and if it's still running
  const savedPid = getOllamaPid();
  if (savedPid && isProcessRunning(savedPid)) {
    // Verify the process is actually Ollama by testing the API
    const timeout = isCodespaces() ? 30000 : 10000; // 30s for Codespaces, 10s otherwise
    const result = run("ollama", ["list"], { 
      stdio: "pipe",
      timeout: timeout
    });
    return result.status === 0;
  }
  
  // No saved PID or process not running, check if ollama is running at all
  const timeout = isCodespaces() ? 30000 : 10000; // 30s for Codespaces, 10s otherwise
  const result = run("ollama", ["list"], { 
    stdio: "pipe",
    timeout: timeout
  });
  return result.status === 0;
}
function installOllama() {
  if (commandExists("ollama")) {
    console.log("Ollama already buckled up, skipping install.");
    return true;
  }
  const platform = process.platform;
  if (platform === "darwin" || platform === "linux") {
    console.log("Internalizing Ollama...");
    const result = spawnSync(
        "sh",
        ["-c", "curl -fsSL https://ollama.com/install.sh | sh"],
        { stdio: "inherit" },
    );
    if (result.status !== 0) {
      console.error("😓 Ollama install failed. Try installing it manually: https://ollama.com/download");
      return false;
    }
    return true;
  }
  if (platform === "win32") {
    if (commandExists("winget", ["--version"])) {
      console.log("Internalizing Ollama via winget....");
      const result = spawnSync(
          "winget",
          [
              "install", "--id", "Ollama.Ollama", "-e",
              "--silent", "--accept-package-agreements", "--accept-source-agreements",
          ],
          { stdio: "inherit" },
      );
      if (result.status === 0) {
        console.log("Ollama is in the clear. You may need to open a new terminal for PATH to update.");
        return true;
      }
      console.error("winget install failed. Falling back to manual download instructions.");
    }
    console.error(
        "Could not auto install Ollama on Windows.\n"
        + "Install it manually: https://ollama.com/download/windows\n"
        + "Or, if you have winget: winget install --id Ollama.Ollama -e",
    );
    return false;
  }

  console.error(`Unrecognized platform '${platform}' Install Ollama manually: https://ollama.com/download`)
  return false;
}

function startOllamaServe() {
  if (isOllamaRunning()) {
    console.log("Ollama is already up and running.");
    return true;
  }
  if (!commandExists("ollama")) {
    console.error("Ollama isn't installed - cannot start it. Run `claw-coder setup` first.");
    return false;
  }
  
  // Kill any existing ollama serve processes to prevent conflicts
  console.log("Ensuring clean ollama startup...");
  clearOllamaPid(); // Clear any stale PID file
  
  if (process.platform === "win32") {
    run("taskkill", ["/F", "/IM", "ollama.exe"], { stdio: "pipe" });
  } else {
    run("pkill", ["-f", "ollama serve"], { stdio: "pipe" });
  }
  sleepSync(2000);
  
  console.log("Initializing 🦙  ollama in the unseen......");
  
  // Configure environment for better stability in resource-constrained environments
  const ollamaEnv = {
    ...process.env,
    // Basic stability settings
    OLLAMA_KEEP_ALIVE: "-1",
    OLLAMA_NUM_LOAD_RETRY: isCodespaces() ? "20" : "10", // More retries in Codespaces
    OLLAMA_LOAD_TIMEOUT: isCodespaces() ? "20m" : "10m", // Longer timeout in Codespaces
    OLLAMA_REQUEST_TIMEOUT: isCodespaces() ? "20m" : "10m", // Longer timeout in Codespaces
    OLLAMA_MAX_QUEUE: isCodespaces() ? "1024" : "512", // Larger queue in Codespaces
    OLLAMA_NUM_PARALLEL: "1", // Keep single parallel for stability
    
    // Codespaces-specific network settings
    OLLAMA_HOST: isCodespaces() ? "0.0.0.0" : "127.0.0.1", // Listen on all interfaces for Codespaces
    OLLAMA_ORIGINS: isCodespaces() ? "*" : "", // Allow all origins for Codespaces
    
    // Memory and resource management for Codespaces
    ...(isCodespaces() ? {
      OLLAMA_DEBUG: "1", // Enable debug logging for troubleshooting
      OLLAMA_LLM_LIBRARY: "cpu", // Force CPU usage in Codespaces
      OLLAMA_GPU_LAYERS: "0", // Disable GPU layers in Codespaces (save memory)
      OLLAMA_F16KV: "1", // Use half-precision KV cache (save memory)
    } : {}),
  };
  
  // Create log directory for debugging
  try {
    const os = require("os");
    const path = require("path");
    const fs = require("fs");
    const logDir = path.join(os.tmpdir(), "claw-coder");
    fs.mkdirSync(logDir, { recursive: true });
    const logFile = path.join(logDir, "ollama.log");
    
    const proc = spawn("ollama", ["serve"], {
      detached: true,
      stdio: ["ignore", "ignore", "ignore"],
      env: ollamaEnv,
    });
    
    // Save the PID for monitoring
    saveOllamaPid(proc.pid);
    proc.unref(); // let it keep running after Node process exits
    
    console.log(`Ollama started with PID: ${proc.pid}`);
    
  } catch (e) {
    // Fallback without logging if directory creation fails
    const proc = spawn("ollama", ["serve"], {
      detached: true,
      stdio: ["ignore", "ignore", "ignore"],
      env: ollamaEnv,
    });
    
    saveOllamaPid(proc.pid);
    proc.unref();
    console.log(`Ollama started with PID: ${proc.pid}`);
  }
  
  // Poll every 500ms for up to 60s in Codespaces, 30s otherwise
  const maxAttempts = isCodespaces() ? 120 : 60; // 60s for Codespaces, 30s otherwise
  let lastCheckTime = Date.now();
  
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    sleepSync(500);
    
    // Check if the process is still running
    const savedPid = getOllamaPid();
    if (savedPid && !isProcessRunning(savedPid)) {
      console.warn(`Ollama process (PID ${savedPid}) died unexpectedly. Attempting restart...`);
      clearOllamaPid();
      
      // Try to restart once
      if (attempt < maxAttempts - 10) { // Leave time for restart attempt
        sleepSync(2000);
        const restartProc = spawn("ollama", ["serve"], {
          detached: true,
          stdio: ["ignore", "ignore", "ignore"],
          env: ollamaEnv,
        });
        saveOllamaPid(restartProc.pid);
        restartProc.unref();
        console.log(`Ollama restarted with PID: ${restartProc.pid}`);
        continue;
      }
    }
    
    if (isOllamaRunning()) {
      console.log("Ollama is behaving.....");
      return true;
    }
    
    // Print progress every 5 seconds
    if (Date.now() - lastCheckTime > 5000) {
      console.log(`Waiting for Ollama to start... (${Math.round((attempt / maxAttempts) * 100)}%)`);
      lastCheckTime = Date.now();
    }
  }

  console.warn("Ollama may still be starting up - if `claw-coder` fails to connect, relax for a few seconds and then try again.");
  console.warn("In Codespaces, Ollama may take longer to start due to resource constraints.");
  return true;
}

function pullDefaultModels(models) {
  for (const model of models) {
    console.log(`Pulling ${model}...`);
    const timeout = isCodespaces() ? 600000 : 300000; // 10 min for Codespaces, 5 min otherwise
    const result = spawnSync("ollama", ["pull", model], { 
      stdio: "inherit",
      timeout: timeout
    });
    if (result.status !== 0) {
      console.warn(`Warning: could not pull ${model}. You can retry later: ollama pull ${model}`);
    }
  }
}

function ensureOllamaReadyForChat() {
  // best-effort auto start before any chat-driving command, in case the
  // machine rebooted since `claw-coder setup` last ran ollama serve.
  if (commandExists("ollama") && !isOllamaRunning()) {
    startOllamaServe();
  }
}

function requireSession() {
  const session = loadSession();
  if (!session) {
    throw new Error("Not logged in. Run: claw-coder login");
  }
  return session;
}

function buildAgentArgs(command, args) {
  const globalOptions = collectGlobalOptions(args);
  const topK = readOption(args, ["--top-k"]);
  const depth = readOption(args, ["--depth"]);
  const language = readOption(args, ["--language"]);
  const cleaned = stripKnownOptions(args);
  const hasFlag = (flag) => args.includes(flag);

  if (command === "claw-coder") {
    ensureOllamaReadyForChat();
    const uiOption = readOption(args, ["--ui"]);
    const chatArgs = ["chat", ...collectDocumentOptions(args)];
    if (uiOption) {
      chatArgs.push("--ui", uiOption);
    }
    return [...globalOptions, ...chatArgs];
  }
  if (command === "models") {
    return [...globalOptions, "models"];
  }
  if (command === "languages") {
    return [...globalOptions, "languages"];
  }
  if (command === "summary" || command === "graph-summary") {
    return [...globalOptions, "graph-summary"];
  }
  if (command === "ingest") {
    if (cleaned.length === 0) {
      throw new Error("ingest needs at least one file or directory path.");
    }
    return [
      ...globalOptions,
      "ingest-paths",
      ...cleaned,
      ...(hasFlag("--no-recursive") ? ["--no-recursive"] : []),
      ...(hasFlag("--no-vector-rag") ? ["--no-vector-rag"] : []),
    ];
  }
  if (command === "ingest-code") {
    if (cleaned.length !== 1) {
      throw new Error("ingest-code needs exactly one file path.");
    }
    return [...globalOptions, "ingest-code", cleaned[0], ...(language ? ["--language", language] : [])];
  }
  if (command === "ingest-pdf") {
    if (cleaned.length !== 1) {
      throw new Error("ingest-pdf needs one file path (.pdf, .txt, or .md).");
    }
    return [...globalOptions, "ingest-pdf", cleaned[0]];
  }
  if (command === "search") {
    const query = cleaned.join(" ").trim();
    if (!query) {
      throw new Error("search needs a query.");
    }
    return [
      ...globalOptions,
      "search-kb",
      query,
      ...(topK ? ["--top-k", topK] : []),
      ...(hasFlag("--no-hybrid-rerank") ? ["--no-hybrid-rerank"] : []),
    ];
  }
  if (command === "graph") {
    const query = cleaned.join(" ").trim();
    if (!query) {
      throw new Error("graph needs a query.");
    }
    return [...globalOptions, "search-graph", query, ...(topK ? ["--top-k", topK] : []), ...(depth ? ["--depth", depth] : [])];
  }
  if (command === "raw") {
    return cleaned;
  }

  throw new Error(`Unknown command: ${command}`);
}
async function main() {
  const args = process.argv.slice(2);
  const command = args[0];
  await pingTelemetry(command || "none");
  const commandArgs = args.slice(1)

  const pkg = require(path.join(packageRoot, "package.json"));
  
  // Check if user needs to complete new version login
  if (!hasCompletedNewVersionLogin() && command !== "login" && command !== "logout" && command !== "--help" && command !== "-h" && command !== "help" && command !== "--version" && command !== "-v" && command !== "setup" && command !== "doctor") {
    const hasOldSession = loadSession() !== null;
    console.log("\n┌─────────────────────────────────────────┐");
    console.log("│     Welcome to the New Claw-Coder!      │");
    console.log("├─────────────────────────────────────────┤");
    console.log("│  This version requires a fresh login      │");
    console.log("│  to unlock all features and capabilities │");
    if (hasOldSession) {
      console.log("├─────────────────────────────────────────┤");
      console.log("│  We detected an existing session. Please  │");
      console.log("│  login again to upgrade to the new version │");
    }
    console.log("├─────────────────────────────────────────┤");
    console.log("│  Please login once to get started:       │");
    console.log("│  Run: claw-coder login                   │");
    console.log("└─────────────────────────────────────────┘\n");
    process.exitCode = 0;
    return;
  }
  
  // If user has completed new version login but session expired, prompt to login again
  if (hasCompletedNewVersionLogin() && !loadSession() && command !== "login" && command !== "logout" && command !== "--help" && command !== "-h" && command !== "help" && command !== "--version" && command !== "-v" && command !== "setup" && command !== "doctor") {
    console.log("\n┌─────────────────────────────────────────┐");
    console.log("│     Session Expired                      │");
    console.log("├─────────────────────────────────────────┤");
    console.log("│  Your session has expired. Please login   │");
    console.log("│  again to continue using Claw-Coder:     │");
    console.log("├─────────────────────────────────────────┤");
    console.log("│  Run: claw-coder login                   │");
    console.log("└─────────────────────────────────────────┘\n");
    process.exitCode = 0;
    return;
  }
  
  const latestVersion = await checkForUpdate();
  if (latestVersion && isNewerVersion(latestVersion, pkg.version)) {
    console.log(`\n  A new version of claw-coder is available: ${pkg.version} → ${latestVersion}`);
    console.log(`  Run \`claw-coder upgrade\` to update.\n`);
  }

  // Handle help commands
  if (command === "--help" || command === "-h" || command === "help") {
    printHelp();
    return;
  }
  
  // Handle claw-coder help specifically
  if (command === "claw-coder" && commandArgs.length > 0 && (commandArgs[0] === "help" || commandArgs[0] === "--help" || commandArgs[0] === "-h")) {
    printHelp();
    return;
  }
  
  // If no command provided, default to chat UI
  if (!command) {
    ensureOllamaReadyForChat();
    const globalOptions = collectGlobalOptions(args);
    const chatArgs = ["chat", ...collectDocumentOptions(args)];
    const uiOption = readOption(args, ["--ui"]);
    if (uiOption) {
      chatArgs.push("--ui", uiOption);
    }
    runAgent([...globalOptions, ...chatArgs]);
    return;
  }
  if (command === "--version" || command === "-v") {
    const pkg = require(path.join(packageRoot, "package.json"));
    console.log(pkg.version);
    return;
  }
  if (command === "setup") {
    runSetup();
    return;
  }
  if (command === "models") {
    runAgent(["models"]);
    return;
  }

  if (command === "doctor") {
    runDoctor();
    return;
  }
  if (command === "embedding") {
    const embeddingModel = commandArgs[0];
  if (!embeddingModel) {
        console.error("Usage: claw embedding <model-name>");
        console.error("Example: claw embedding nomic-embed-text");
        process.exitCode = 1;
        return;
  }
  runAgent(["--embedding-model", embeddingModel, "chat"]);
  return;
}

  if (command === "login") {
    const provider = commandArgs[0] || "github";
    login(provider)
        .then((session) => {
        console.log(`\nLogged in as ${session.user?.email}`);
        console.log("Run `claw-coder` to start using Claw-Coder.");
        })
        .catch((err) => {
        console.error(`Login failed: ${err.message}`);
        process.exitCode = 1;
        });
    return;
}
  if (command === "upgrade") {
  runUpgrade();
  return;
}
  if (command === "logout") {
    clearSession();
    console.log("Logged out. Run `claw-coder login` to log in again.");
    console.log("Note: You'll need to complete the new version login again.");
    return;
}
  if (command === "telemetry") {
  const sub = commandArgs[0];
  if (sub === "on" || sub === "off") {
    fs.mkdirSync(path.dirname(getTelemetryConsentFile()), { recursive: true });
    fs.writeFileSync(getTelemetryConsentFile(), JSON.stringify({ consent: sub === "on", decidedAt: Date.now() }), "utf8");
    console.log(`Telemetry ${sub === "on" ? "enabled" : "disabled"}.`);
  } else {
    const c = getTelemetryConsent();
    console.log(`Telemetry: ${c === null ? "not yet decided" : c ? "enabled" : "disabled"}`);
  }
  return;
}
  if (command === "whoami") {
    const session = loadSession();
  if (!session) {
    console.log("Not logged in. Run: claw-coder login");
  } else {
        console.log(`Logged in as: ${session.user?.email}`);
        const exp = new Date(session.expires_at * 1000).toLocaleString();
        console.log(`Session expires: ${exp}`);
    }
  return;
    }
   if (command === "usage") {
    let session;
    try {
      session = requireSession();
    } catch (err) {
      console.error(err.message);
      process.exitCode = 1;
      return;
    }


    apiFetch("/usage", session)
      .then((data) => {
        const plan = data.plan || "starter";
        const planEmoji = ["plus", "pro", "max"].includes(plan) ? "⚡" : "🆓";
        const planColor = ["plus", "pro", "max"].includes(plan) ? "\x1b[1;36m" : "\x1b[1;33m"; // cyan for pro, yellow for free
        const reset = "\x1b[0m";

        const creditsSpent = data.credits_spent_month || 0;
        const creditsGranted = data.credits_granted_month || 0;
        const currentBalance = data.credits || 0;
        const totalCredits = creditsGranted + currentBalance;
        const creditUsagePct = totalCredits > 0 ? Math.round((creditsSpent / totalCredits) * 100) : 0;

        console.log(`\n${"═".repeat(54)}`);
        console.log(`  📊 CLAW CODER USAGE  ${data.month}  ${planEmoji} ${planColor}${plan.toUpperCase()}${reset} PLAN`);
        console.log(`${"═".repeat(54)}`);
        console.log(`  💰 Current Balance: ${currentBalance} credits`);
        console.log(`  📈 Credits This Month: +${creditsGranted}  -${creditsSpent}  (${creditUsagePct}% used)`);
        console.log(`${"═".repeat(54)}\n`);

        const usage = data.usage || {};
        const tools = Object.keys(usage).sort();

        if (tools.length === 0) {
          console.log("  🎉 No tools used this month yet.\n");
          return;
        }

        // column widths
        const nameWidth = 26;
        const barWidth  = 12;

        console.log(`  ${"Tool".padEnd(nameWidth)} ${"Usage".padEnd(barWidth)}  Count      Remaining   %`);
        console.log("  " + "─".repeat(nameWidth + barWidth + 28));

        for (const tool of tools) {
          const { used, limit, remaining } = usage[tool];
          // For PRO plans, treat 999999 as the soft limit (400) for display purposes
          // The server should return 400, but if it returns 999999, we'll handle it
          const effectiveLimit = (plan === "pro" && limit >= 999999) ? 400 : limit;
          const isUnlimited = limit >= 999999 && plan !== "pro";
          const pct   = isUnlimited ? 0 : Math.min(1, used / effectiveLimit);
          const filled = Math.round(pct * barWidth);
          const pctStr = isUnlimited ? "N/A" : `${Math.round(pct * 100)}%`;
          
          // Color-coded progress bar
          let barColor = "\x1b[32m"; // green
          if (pct >= 0.8) barColor = "\x1b[31m"; // red
          else if (pct >= 0.5) barColor = "\x1b[33m"; // yellow
          
          const bar   = isUnlimited
            ? "∞ unlimited  "
            : `${barColor}█${reset}`.repeat(filled).padEnd(barWidth, "░");

          const countStr    = isUnlimited ? `${used}` : `${used}/${effectiveLimit}`;
          const remainStr   = isUnlimited ? "∞" : `${Math.max(0, effectiveLimit - used)} left`;

          // warn if over 80% of soft limit
          const warn = !isUnlimited && pct >= 0.8 ? " ⚠️" : "";

          console.log(
            `  ${tool.padEnd(nameWidth)} ${bar}  ${countStr.padEnd(12)}${remainStr.padEnd(12)}${pctStr.padEnd(6)}${warn}`
          );
        }

        console.log("\n" + "─".repeat(54));
        if (plan === "starter") {
          console.log("  📝 Starter plan is used first. After that, paid credits are used.");
          console.log("  🚀 Run `claw-coder upgrade-plan` to subscribe or `claw-coder topup` for extra credits.\n");
        } else {
          console.log("  ⚡ Pro plan: Each tool has a soft limit that are within the paid credits.");
          console.log("  💳 After reaching the soft limit the available or the remaining credits will still be consumed if there are no remaining credits you can run claw-coder topup or claw-coder upgrade-plan anytime.\n");
        }
      })
      .catch((err) => {
        console.error(`❌ Could not fetch usage: ${err.message}`);
        console.error("⏳ If this is Render free hosting, wait a few seconds and retry.");
        process.exitCode = 1;
      });
    return;
  }

  if (command === "credits") {
    let session;
    try {
      session = requireSession();
    } catch (err) {
      console.error(err.message);
      process.exitCode = 1;
      return;
    }
    apiFetch("/plan", session)
      .then((data) => {
        const plan = String(data.plan || "free").toUpperCase();
        const planEmoji = plan === "PRO" ? "⚡" : "🆓";
        const planColor = plan === "PRO" ? "\x1b[1;36m" : "\x1b[1;33m"; // cyan for pro, yellow for free
        const reset = "\x1b[0m";
        const credits = data.credits || 0;
        const creditsSpent = data.credits_spent_month || 0;
        const creditsGranted = data.credits_granted_month || 0;
        const usagePct = data.usage_percentage || 0;
        const totalCredits = creditsGranted + credits;

        console.log(`\n${"═".repeat(50)}`);
        console.log(`  💳 CREDIT BALANCE  ${planEmoji} ${planColor}${plan}${reset} PLAN`);
        console.log(`${"═".repeat(50)}`);
        console.log(`  💰 Available Credits: ${credits}`);
        console.log(`  📋 Plan: ${planColor}${plan}${reset}`);
        console.log(`${"─".repeat(50)}`);
        console.log(`  📈 This Month: +${creditsGranted} granted  -${creditsSpent} spent  (${usagePct}% used)`);
        console.log(`  📊 Total Available: ${totalCredits} credits`);
        console.log(`${"─".repeat(50)}`);
        console.log("  📝 Limited tools use free monthly allowance first,");
        console.log("  then paid credits are automatically consumed.\n");
        
        if (plan === "FREE" && credits < 100) {
          console.log("  ⚠️ Low credits! Consider upgrading to Pro for more features.");
          console.log("  🚀 Run `claw-coder upgrade-plan` to subscribe or `claw-coder topup` for extra credits.\n");
        } else if (plan === "PRO" && credits < 50) {
          console.log("  ⚠️ Running low on credits!");
          console.log("  🚀 Run `claw-coder topup` for extra credits.\n");
        }
      })
      .catch((err) => {
        console.error(`❌ Could not fetch credits: ${err.message}`);
        console.error("⏳ If this is Render free hosting, wait a few seconds and retry.");
        process.exitCode = 1;
      });
    return;
  }


  const PLAN_INFO = {
  plus: { label: "Claw-Coder Plus", price: "$25/month", desc: "1000 tool credits/month, no workspace access" },
  pro:     { label: "Claw-Coder Pro",     price: "$50/month", desc: "10000 tool credits/month + 1000 workspace credits" },
  max: { label: "Claw-Coder Max", price: "$100/month", desc: "100000 tool credits/month + 20000 workspace credits"}
};

  if (command === "upgrade-plan") {
    let session;
    try {
      session = requireSession();
    } catch (err) {
      console.error(err.message);
      process.exitCode = 1;
      return;
    }

    let plan = (commandArgs[0] || "").toLowerCase();

    if (!plan) {
      console.log("\nAvailable plans:\n");
      for (const [key, info] of Object.entries(PLAN_INFO)) {
        console.log(`  ${key.padEnd(8)} ${info.label.padEnd(16)} ${info.price.padEnd(12)} ${info.desc}`);
      }
      console.log("");
      const readline = require("node:readline");
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      plan = await new Promise((resolve) => {
        rl.question("Which plan? (plus/pro/max): ", (answer) => {
          rl.close();
          resolve(answer.trim().toLowerCase());
        });
      });
    }

    if (!PLAN_INFO[plan]) {
      console.error(`Unknown plan '${plan}'. Choose: ${Object.keys(PLAN_INFO).join(", ")}`);
      process.exitCode = 1;
      return;
    }

    console.log(`Creating checkout for ${PLAN_INFO[plan].label} (${PLAN_INFO[plan].price})...`);
    apiFetch("/checkout", session, {
      method: "POST",
      body: JSON.stringify({ mode: "subscription", plan }),
    })
      .then((data) => {
        if (!data.checkout_url) {
          throw new Error("The billing server did not return a checkout URL.");
        }
        console.log(`\n  Plan: ${PLAN_INFO[plan].label}`);
        console.log(`  Monthly credits: ${data.credits}`);
        console.log(`  Checkout: ${data.checkout_url}\n`);
        const opener = process.platform === "darwin" ? "open"
          : process.platform === "win32" ? "start"
          : "xdg-open";
        try {
          spawnSync(opener, [data.checkout_url], { stdio: "ignore", shell: process.platform === "win32" });
        } catch {}
      })
      .catch((err) => {
        console.error(`Could not create checkout: ${err.message}`);
        console.error("If this is Render free hosting, wait a few seconds and retry.");
        process.exitCode = 1;
      });
    return;
  }


  if (command === "clear-storage") {
    const fs = require("node:fs");
    const os = require("node:os");
    const path = require("node:path");
    
    const sessionDir = path.join(os.homedir(), ".claw-coder");
    const sessionFile = path.join(sessionDir, "session.json");
    const loginVersionFile = path.join(sessionDir, "login_version");
    const telemetryFile = path.join(sessionDir, "telemetry_consent.json");
    const updateCheckFile = path.join(sessionDir, "update_check.json");
    const deviceIdFile = path.join(sessionDir, "device_id");
    
    console.log("\n┌─────────────────────────────────────────┐");
    console.log("│     Clearing Claw-Coder Storage              │");
    console.log("├─────────────────────────────────────────┤");
    
    let clearedCount = 0;
    
    if (fs.existsSync(sessionFile)) {
      fs.unlinkSync(sessionFile);
      console.log("│  ✓ Session data cleared                   │");
      clearedCount++;
    }
    
    if (fs.existsSync(loginVersionFile)) {
      fs.unlinkSync(loginVersionFile);
      console.log("│  ✓ Login version flag cleared              │");
      clearedCount++;
    }
    
    if (fs.existsSync(telemetryFile)) {
      fs.unlinkSync(telemetryFile);
      console.log("│  ✓ Telemetry consent cleared               │");
      clearedCount++;
    }
    
    if (fs.existsSync(updateCheckFile)) {
      fs.unlinkSync(updateCheckFile);
      console.log("│  ✓ Update check data cleared               │");
      clearedCount++;
    }
    
    if (fs.existsSync(deviceIdFile)) {
      fs.unlinkSync(deviceIdFile);
      console.log("│  ✓ Device ID cleared                       │");
      clearedCount++;
    }
    
    console.log("├─────────────────────────────────────────┤");
    console.log(`│  Total items cleared: ${clearedCount}                │`);
    console.log("└─────────────────────────────────────────┘\n");
    console.log("You'll need to login again: claw-coder login");
    return;
  }

  if (command === "topup") {
    let session;
    try {
      session = requireSession();
    } catch (err) {
      console.error(err.message);
      process.exitCode = 1;
      return;
    }
    console.log(`\n${"═".repeat(50)}`);
    console.log("  💳 CREATING CHECKOUT FOR CREDIT TOP-UP");
    console.log(`${"═".repeat(50)}`);
    console.log("  💰 Extra pay-as-you-go credits");
    console.log("  🚀 No subscription required");
    console.log("  📦 One-time purchase");
    console.log(`${"─".repeat(50)}\n`);
    
    apiFetch("/checkout", session, { method: "POST", body: JSON.stringify({ mode: "topup" }) })
      .then((data) => {
        if (!data.checkout_url) {
          throw new Error("The billing server did not return a checkout URL.");
        }
        console.log(`  ✅ Checkout created successfully!`);
        console.log(`  💳 Extra credits: ${data.credits}`);
        console.log(`  🔗 Checkout URL: ${data.checkout_url}\n`);
        console.log("  🌐 Opening checkout page in your browser...\n");
        
        const opener = process.platform === "darwin" ? "open"
          : process.platform === "win32" ? "start"
          : "xdg-open";
        try {
          spawnSync(opener, [data.checkout_url], {
            stdio: "ignore",
            shell: process.platform === "win32",
          });
        } catch {}
        
        console.log("  📝 After payment, your credits will be added automatically.");
        console.log("  🔄 Run `claw-coder credits` to check your balance after payment.\n");
      })
      .catch((err) => {
        console.error(`❌ Could not create top-up checkout: ${err.message}`);
        console.error("⏳ If this is Render free hosting, wait a few seconds and retry.");
        process.exitCode = 1;
      });
    return;
  }

// ── AUTH GATE ──────────────────────────────────────────────
// skip auth for setup/doctor/help (they don't touch the agent)
  const NO_AUTH_COMMANDS = new Set(["setup", "doctor", "help", "--help", "-h", "login", "logout", "whoami", "--version", "-v", "usage", "credits", "buy", "topup", "models", "telemetry", "claw-coder", "clear-storage"]);
  if (!NO_AUTH_COMMANDS.has(command)) {
    const session = loadSession();
  if (!session) {
    console.error("\n┌─────────────────────────────────────────┐");
    console.error("│         Authentication Required           │");
    console.error("├─────────────────────────────────────────--┤");
    console.error("│  You need to log in to use this command   │");
    console.error("│                                           │");
    console.error("│  Run: claw-coder login                   │");
    console.error("│                                           │");
    console.error("│  This will enable:                        │");
    console.error("│  • Full Claw-Coder capabilities           │");
    console.error("│  • Cloud tools access                     │");
    console.error("│  • Usage tracking & credits               │");
    console.error("└─────────────────────────────────────────--┘\n");
    process.exitCode = 1;
    return;
  }
  // inject user identity into env so python can read it if needed
  process.env.CLAW_USER_EMAIL = session.user?.email || "";
  process.env.CLAW_USER_ID    = session.user?.id    || "";
}
// ──────────────────────────────────────────────────────────
  // Handle claw-coder command to launch chat UI
  if (command === "claw-coder") {
    ensureOllamaReadyForChat();
    const globalOptions = collectGlobalOptions(commandArgs);
    const chatArgs = ["chat", ...collectDocumentOptions(commandArgs)];
    const uiOption = readOption(commandArgs, ["--ui"]);
    if (uiOption) {
      chatArgs.push("--ui", uiOption);
    }
    runAgent([...globalOptions, ...chatArgs]);
    return;
  }

  // ← KNOWN_COMMANDS must be INSIDE main() so command is defined
  const KNOWN_COMMANDS = new Set([
    "models", "ingest", "ingest-code", "ingest-pdf", "search",
    "graph", "summary", "graph-summary", "languages",
    "setup", "doctor", "raw", "embedding","usage", "credits", "upgrade-plan", "topup",
    "telemetry"
  ]);

  if (!KNOWN_COMMANDS.has(command)) {
    // If it's "chat", show error message
    if (command === "chat") {
      console.error("Error: 'claw chat' is no longer supported. Use 'claw-coder' instead.");
      console.error("Example: claw-coder");
      console.error("Example: claw-coder --pdf document.pdf");
      process.exitCode = 1;
      return;
    }
    
    // Otherwise treat as model name
    const embeddingModel = commandArgs[0];  // optional second arg
    const agentArgs = ["--model", command];
    if (embeddingModel && !embeddingModel.startsWith("--")) {
      agentArgs.push("--embedding-model", embeddingModel);
    }
    agentArgs.push("chat");
    runAgent(agentArgs);
    return;
  }
  try {
    runAgent(buildAgentArgs(command, commandArgs));
  } catch (error) {
    console.error(error.message);
    console.error("Run `claw --help` for usage.");
    process.exitCode = 1;
  }
}


main();
