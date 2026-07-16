#!/usr/bin/env node
"use strict";

const { spawnSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { login, loadSession, clearSession } = require("./auth");
const DEFAULT_OLLAMA_MODELS = ["llama3.2:1b", "qwen3-embedding:4b", "translategemma:4b"]

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
Claw Coder

Usage:
  claw <command> [options]

Commands:
  chat [--pdf <file>...]         Start interactive chat (optionally preload PDFs)
  models                         List local Ollama models
  ingest <paths...>              Ingest files/directories into graph + vector RAG
  ingest-code <file>             Ingest one source file
  ingest-pdf <file>              Ingest a PDF or text document (.pdf, .txt, .md)
  search <query>               Search vector RAG with graph reranking
  graph <query>                Search the knowledge graph only
  summary                      Show graph node/edge counts
  languages                    Show Tree-sitter language support
  setup                        Install Python dependencies for Claw Coder
  doctor                       Check local Node/Python/Ollama setup
  usage                        Show this month's cloud tool usage
  credits                      Show paid credit balance
  buy                          Subscribe for $30/month credits
  topup                        Buy extra pay-as-you-go credits

Common options:
  --top-k <n>                  Number of results to return
  --depth <n>                  Graph traversal depth for graph search
  --graph <file>               Knowledge graph JSON path
  --db <dir>                   ChromaDB directory
  --collection <name>          ChromaDB collection
  --model <name>               Ollama chat model
  --embedding-model <name>     Ollama embedding model

Examples:
  claw setup
  claw doctor
  claw ingest .
  claw graph "imports tree_sitter" --depth 2
  claw search "where is reranking implemented?" --top-k 5
  claw chat
  claw chat --pdf report.pdf --pdf notes.txt
  claw models
  claw qwen2.5-coder:7b        Start chat with any local Ollama model
  claw embedding <model>       Start a model for the embeddings part of the agent
  login [provider]             Log in via OAuth (default: github)
  logout                       Clear saved session
  whoami                       Show current logged-in user
  usage                        Show usage and remaining free allowance
  credits                      Show paid credit balance
  buy                          Open checkout for the $30/month plan
  topup                        Open checkout for extra credits

`;

function printHelp() {
  console.log(HELP.trimStart());
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || process.cwd(),
    stdio: options.stdio || "inherit",
    env: process.env,
    encoding: "utf8",
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
  const result = run(command, args, { stdio: "pipe" });
  return result.status === 0;
}

function findPython() {
  if (process.env.CLAW_PYTHON) {
    return process.env.CLAW_PYTHON;
  }

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

  for (const candidate of venvCandidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  if (commandExists("python3")) {
    return "python3";
  }
  if (commandExists("python")) {
    return "python";
  }
  return null;
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

function runAgent(agentArgs) {
  const python = findPython();
  if (!python) {
    console.error("Python was not found. Install Python 3 or set CLAW_PYTHON=/path/to/python.");
    process.exitCode = 1;
    return;
  }

  const result = run(python, [pythonAgent, ...agentArgs], { cwd: process.cwd() });
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
        { command: "python3", prefixArgs: [] },
        { command: "python", prefixArgs: [] },
      ]
    : [
        { command: "python3.12", prefixArgs: [] },
        { command: "python3.11", prefixArgs: [] },
        { command: "python3", prefixArgs: [] },
        { command: "python", prefixArgs: [] },
      ];

  for (const spec of specs) {
    const versionArgs = spec.prefixArgs.length ? [...spec.prefixArgs, "--version"] : ["--version"];
    if (commandExists(spec.command, versionArgs)) {
      return spec;
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
      + "Remove ./venv and run `claw setup` again so it can create a Python 3.12 environment.",
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
    console.error("Python dependency install failed. Check your network connection and run `claw setup` again.");
  }
  console.log("");
  installOllama();
  startOllamaServe();
  pullDefaultModels(DEFAULT_OLLAMA_MODELS);
  process.exitCode = 0;

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
      console.log("NO  Python packages: missing; run `claw setup`");
    }
  }
}

function getApiUrl() {
  return (process.env.RATE_LIMIT_API_URL || "https://claw-coder-3.onrender.com").replace(/\/$/, "");
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
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { detail: text };
    }
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
function sleepSync(ms) {
  const platform = process.platform;
  if (platform === "win32") {
    spawnSync("powershell", ["-NoProfile", "-Command", `Start-Sleep -Milliseconds ${ms}`], { stdio: "ignore" });
  } else {
    spawnSync("sleep", [(ms / 1000).toString()], { stdio: "ignore" });
  }
}

function isOllamaRunning() {
  // ollama list will only work if daemon is actually reachable
  const result = run("ollama", ["list"], { stdio: "pipe" });
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
    console.error("Ollama isn't installed - cannot start it. Run `claw setup` first.");
    return false;
  }
  console.log("Initializing 🦙  ollama in the unseen......");
  const proc = spawn("ollama", ["serve"], {
    detached: true,
    stdio: "ignore"
  });
  proc.unref(); // let it keep running after Node process exits
  // Poll every 500ms for up to 5s instead of hard-spinning the CPU.
  for (let attempt = 0; attempt < 10; attempt += 1) {
    sleepSync(500);
    if (isOllamaRunning()) {
      console.log("Ollama is behaving.....");
      return true;
    }
  }

  console.warn("Ollama may still be starting up - if `claw chat` fails to connect, relax for a few seconds and then try again.");
  return true;
}

function pullDefaultModels(models) {
  for (const model of models) {
    console.log(`Pulling ${model}...`);
    const result = spawnSync("ollama", ["pull", model], { stdio: "inherit" });
    if (result.status !== 0) {
      console.warn(`Warning: could not pull ${model}. You can retry later: ollama pull ${model}`);
    }
  }
}

function ensureOllamaReadyForChat() {
  // best-effort auto start before any chat-driving command, in case the
  // machine rebooted since `claw setup` last ran ollama serve.
  if (commandExists("ollama") && !isOllamaRunning()) {
    startOllamaServe();
  }
}

function requireSession() {
  const session = loadSession();
  if (!session) {
    throw new Error("Not logged in. Run: claw login");
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

  if (command === "chat") {
    ensureOllamaReadyForChat();
    return [...globalOptions, "chat", ...collectDocumentOptions(args)];
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
function main() {
  const args = process.argv.slice(2);
  const command = args[0];
  const commandArgs = args.slice(1);

  if (!command || command === "--help" || command === "-h" || command === "help") {
    printHelp();
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
  // --- paste this block right after the "doctor" check ---

  if (command === "login") {
    const provider = commandArgs[0] || "github";
    login(provider)
        .then((session) => {
        console.log(`\nLogged in as ${session.user?.email}`);
        console.log("Run `claw chat` or any claw command to start.");
        })
        .catch((err) => {
        console.error(`Login failed: ${err.message}`);
        process.exitCode = 1;
        });
    return;
}

  if (command === "logout") {
    clearSession();
    console.log("Logged out. Run `claw login` to log in again.");
    return;
}

  if (command === "whoami") {
    const session = loadSession();
  if (!session) {
    console.log("Not logged in. Run: claw login");
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
        const plan = data.plan || "free";
        console.log(`\n  Claw Coder usage  ${data.month}  ${plan.toUpperCase()} plan`);
        console.log(`  Paid credits: ${data.credits || 0}\n`);

        const usage = data.usage || {};
        const tools = Object.keys(usage).sort();

        if (tools.length === 0) {
          console.log("  No tools used this month yet.\n");
          return;
        }

        // column widths
        const nameWidth = 32;
        const barWidth  = 12;

        console.log(
          `  ${"Tool".padEnd(nameWidth)} ${"Usage".padEnd(barWidth)}  Count     Remaining`
        );
        console.log("  " + "─".repeat(nameWidth + barWidth + 22));

        for (const tool of tools) {
          const { used, limit, remaining } = usage[tool];
          const isPro = limit >= 999999;
          const pct   = isPro ? 0 : Math.min(1, used / limit);
          const filled = Math.round(pct * barWidth);
          const bar   = isPro
            ? "∞ unlimited  "
            : "█".repeat(filled).padEnd(barWidth, "░");

          const countStr    = isPro ? `${used}` : `${used}/${limit}`;
          const remainStr   = isPro ? "∞" : `${remaining} left`;

          // warn if over 80%
          const warn = !isPro && pct >= 0.8 ? " ⚠" : "";

          console.log(
            `  ${tool.padEnd(nameWidth)} ${bar}  ${countStr.padEnd(10)}${remainStr}${warn}`
          );
        }

        if (plan === "free") {
          console.log("\n  Free allowance is used first. After that, paid credits are used.");
          console.log("  Run `claw buy` to subscribe or `claw topup` for extra credits.\n");
        } else {
          console.log("\n  All tools unlimited on Pro plan.\n");
        }
      })
      .catch((err) => {
        console.error(`Could not fetch usage: ${err.message}`);
        console.error("If this is Render free hosting, wait a few seconds and retry.");
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
        console.log(`\n  Plan: ${String(data.plan || "free").toUpperCase()}`);
        console.log(`  Paid credits: ${data.credits || 0}`);
        console.log("  Limited tools use free monthly allowance first, then paid credits.\n");
      })
      .catch((err) => {
        console.error(`Could not fetch credits: ${err.message}`);
        console.error("If this is Render free hosting, wait a few seconds and retry.");
        process.exitCode = 1;
      });
    return;
  }

  if (command === "buy") {
    let session;
    try {
      session = requireSession();
    } catch (err) {
      console.error(err.message);
      process.exitCode = 1;
      return;
    }
    console.log("Creating checkout for the $14.99/month Claw Coder plan...");
    apiFetch("/checkout", session, { method: "POST", body: JSON.stringify({}) })
      .then((data) => {
        if (!data.checkout_url) {
          throw new Error("The billing server did not return a checkout URL.");
        }
        console.log(`\n  Monthly credits: ${data.credits}`);
        console.log(`  Checkout: ${data.checkout_url}\n`);
        const opener = process.platform === "darwin" ? "open"
          : process.platform === "win32" ? "start"
          : "xdg-open";
        try {
          spawnSync(opener, [data.checkout_url], {
            stdio: "ignore",
            shell: process.platform === "win32",
          });
        } catch {}
      })
      .catch((err) => {
        console.error(`Could not create checkout: ${err.message}`);
        console.error("If this is Render free hosting, wait a few seconds and retry.");
        process.exitCode = 1;
      });
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
    console.log("Creating checkout for extra Claw Coder credits...");
    apiFetch("/checkout", session, { method: "POST", body: JSON.stringify({ mode: "topup" }) })
      .then((data) => {
        if (!data.checkout_url) {
          throw new Error("The billing server did not return a checkout URL.");
        }
        console.log(`\n  Extra credits: ${data.credits}`);
        console.log(`  Checkout: ${data.checkout_url}\n`);
        const opener = process.platform === "darwin" ? "open"
          : process.platform === "win32" ? "start"
          : "xdg-open";
        try {
          spawnSync(opener, [data.checkout_url], {
            stdio: "ignore",
            shell: process.platform === "win32",
          });
        } catch {}
      })
      .catch((err) => {
        console.error(`Could not create top-up checkout: ${err.message}`);
        console.error("If this is Render free hosting, wait a few seconds and retry.");
        process.exitCode = 1;
      });
    return;
  }

// ── AUTH GATE ──────────────────────────────────────────────
// skip auth for setup/doctor/help (they don't touch the agent)
  const NO_AUTH_COMMANDS = new Set(["setup", "doctor", "help", "--help", "-h", "login", "logout", "whoami", "--version", "-v", "usage", "credits", "buy", "topup", "models"]);
  if (!NO_AUTH_COMMANDS.has(command)) {
    const session = loadSession();
  if (!session) {
    console.error("\nNot logged in. Run: claw login\n");
    process.exitCode = 1;
    return;
  }
  // inject user identity into env so python can read it if needed
  process.env.CLAW_USER_EMAIL = session.user?.email || "";
  process.env.CLAW_USER_ID    = session.user?.id    || "";
}
// ──────────────────────────────────────────────────────────
  // ← KNOWN_COMMANDS must be INSIDE main() so command is defined
  const KNOWN_COMMANDS = new Set([
    "chat", "models", "ingest", "ingest-code", "ingest-pdf", "search",
    "graph", "summary", "graph-summary", "languages",
    "setup", "doctor", "raw", "embedding","usage", "credits", "buy", "topup-> beta"
  ]);

  if (!KNOWN_COMMANDS.has(command)) {
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
