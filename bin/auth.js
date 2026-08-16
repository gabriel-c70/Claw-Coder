"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

// Polyfill fetch for Node.js < 18
if (!global.fetch) {
  try {
    const nodeFetch = require("node-fetch");
    global.fetch = nodeFetch;
  } catch (e) {
    console.error("Error: Node.js version too old. fetch API requires Node.js 18+.");
    console.error("Please upgrade Node.js or install node-fetch: npm install node-fetch");
    process.exit(1);
  }
}

const SESSION_DIR = path.join(os.homedir(), ".claw-coder");
const SESSION_FILE = path.join(SESSION_DIR, "session.json");
// Increment this whenever a release must re-authenticate every installed CLI.
// The old marker did not contain a version, so it could only force login once.
const REQUIRED_LOGIN_VERSION = 2;
const LOGIN_VERSION_FILE = path.join(SESSION_DIR, "login_version");
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60; // soft local TTL; GitHub may revoke earlier

const BAKED_CONFIG = {
  supabaseUrl:    "https://nqbrdafvdfntxvhbyama.supabase.co",
  anonKey:        "sb_publishable_fKGO3iZ6nCEtPUqPsQb_nQ_jIXwMtCJ",
  githubClientId: "Ov23li6ZYK8WmGloMm90",
};

// ─────────────────────────────────────────────────────────────────────────────

function loadEnvFile() {
  const envFile = path.join(path.resolve(__dirname, ".."), ".env");
  if (fs.existsSync(envFile)) {
    for (const line of fs.readFileSync(envFile, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const [key, ...rest] = trimmed.split("=");
      if (key && rest.length && !process.env[key.trim()]) {
        process.env[key.trim()] = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
      }
    }
  }
}

function getSupabaseConfig() {
  loadEnvFile();

  return {
    url:            process.env.SUPABASE_URL      || BAKED_CONFIG.supabaseUrl,
    anonKey:        process.env.SUPABASE_ANON_KEY || BAKED_CONFIG.anonKey,
    serviceKey:     process.env.SUPABASE_SERVICE_KEY || null,  // env-only, always
    githubClientId: process.env.GITHUB_CLIENT_ID  || BAKED_CONFIG.githubClientId,
  };
}

function saveSession(session) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  fs.writeFileSync(SESSION_FILE, JSON.stringify(session, null, 2), "utf8");
  try { fs.chmodSync(SESSION_FILE, 0o600); } catch {}
}

function loadSession() {
  if (!fs.existsSync(SESSION_FILE)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(SESSION_FILE, "utf8"));

    // Honor expiry strictly — never silently extend it locally.
    if (data.expires_at && Date.now() / 1000 > data.expires_at - 60) {
      if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
      return null;
    }

    if (!data.access_token && !data.github_token) {
      if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
      return null;
    }

    return data;
  } catch {
    return null;
  }
}

function clearSession({ keepLoginVersion = false } = {}) {
  if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
  if (!keepLoginVersion && fs.existsSync(LOGIN_VERSION_FILE)) fs.unlinkSync(LOGIN_VERSION_FILE);
}

function hasCompletedNewVersionLogin() {
  try {
    return Number.parseInt(fs.readFileSync(LOGIN_VERSION_FILE, "utf8"), 10) >= REQUIRED_LOGIN_VERSION;
  } catch {
    return false;
  }
}

function markNewVersionLoginComplete() {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  fs.writeFileSync(LOGIN_VERSION_FILE, String(REQUIRED_LOGIN_VERSION), "utf8");
  try { fs.chmodSync(LOGIN_VERSION_FILE, 0o600); } catch {}
}

function getApiUrl() {
  return process.env.RATE_LIMIT_API_URL || "https://claw-coder-3.onrender.com";
}
function getDeviceId() {
  const idFile = path.join(SESSION_DIR, "device_id");
  try {
    return fs.readFileSync(idFile, "utf8").trim();
  } catch {
    // Fallback for older Node.js versions that don't have crypto.randomUUID()
    let id;
    try {
      id = require("crypto").randomUUID();
    } catch (e) {
      // Manual UUID generation for older Node.js
      id = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
      });
    }
    fs.mkdirSync(path.dirname(idFile), { recursive: true });
    fs.writeFileSync(idFile, id, "utf8");
    return id;
  }
}
async function logErrorToSupabase(error, context = {}) {
  try {
    const { url: supabaseUrl, anonKey } = getSupabaseConfig();
    await fetch(`${supabaseUrl}/rest/v1/error_logs`, {
      method: "POST",
      headers: {
        "apikey": anonKey,
        "Content-Type": "application/json",
        "Authorization": `Bearer ${anonKey}`,
      },
      body: JSON.stringify({
        error: error,
        context: context,
        timestamp: new Date().toISOString(),
        device_id: getDeviceId(),
      }),
    });
  } catch (e) {
    // Silent fail - don't break auth if error logging fails
  }
}

function openVerificationUrl(url) {
  try {
    if (process.platform === "darwin") {
      execFileSync("open", [url], { stdio: "ignore" });
    } else if (process.platform === "win32") {
      execFileSync("cmd", ["/c", "start", "", url], { stdio: "ignore", windowsHide: true });
    } else {
      execFileSync("xdg-open", [url], { stdio: "ignore" });
    }
    return true;
  } catch {
    return false;
  }
}

function printBox(lines) {
  const width = Math.max(...lines.map((line) => line.length), 40);
  const top = `┌${"─".repeat(width + 2)}┐`;
  const bottom = `└${"─".repeat(width + 2)}┘`;
  console.log(top);
  for (const line of lines) {
    console.log(`│ ${line.padEnd(width)} │`);
  }
  console.log(bottom);
}

async function login(provider = "github") {
  const normalized = String(provider || "github").trim().toLowerCase();
  if (normalized !== "github") {
    throw new Error(
      `Unsupported login provider '${provider}'. Only GitHub is supported right now.\n` +
      "Run: claw-coder login"
    );
  }

  const { githubClientId } = getSupabaseConfig();

  if (!githubClientId || githubClientId === "your-github-client-id") {
    throw new Error(
      "GITHUB_CLIENT_ID is not set.\n" +
      "Add it to your .env file: GITHUB_CLIENT_ID=your-client-id"
    );
  }

  console.log("");
  printBox([
    "Claw-Coder Login",
    "Authenticating via GitHub OAuth",
    "Works with any GitHub account",
  ]);
  console.log("");

  const deviceRes = await fetch("https://github.com/login/device/code", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({ client_id: githubClientId, scope: "read:user user:email" }),
  });
  const device = await deviceRes.json();

  if (device.error) {
    throw new Error(
      `GitHub device flow error: ${device.error}\n` +
      `${device.error_description || ""}\n\n` +
      `Fix: Go to github.com → Developer Settings → OAuth Apps → your app\n` +
      `     and tick the "Enable Device Flow" checkbox.`
    );
  }
  if (!device.verification_uri) {
    throw new Error(
      `GitHub returned unexpected response: ${JSON.stringify(device)}\n` +
      `Check your GITHUB_CLIENT_ID in .env is correct.`
    );
  }

  printBox([
    "Step 1: Open this link in your browser",
    device.verification_uri,
    "Step 2: Enter this code",
    device.user_code,
  ]);
  console.log("");

  if (openVerificationUrl(device.verification_uri)) {
    console.log("✓ Browser opened automatically\n");
  } else {
    console.log("ℹ  Could not open browser automatically. Please open the link manually.\n");
  }

  console.log("⏳  Waiting for you to approve in GitHub...\n");
  const pollInterval = (device.interval || 5) * 1000;
  const expires = Date.now() + device.expires_in * 1000;

  while (Date.now() < expires) {
    await new Promise(r => setTimeout(r, pollInterval));

    const tokenRes = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        client_id: githubClientId,
        device_code: device.device_code,
        grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      }),
    });
    const tokenData = await tokenRes.json();

    if (tokenData.error === "authorization_pending") {
      process.stdout.write(".");
      continue;
    }
    if (tokenData.error === "slow_down") {
      await new Promise(r => setTimeout(r, 5000));
      continue;
    }
    if (tokenData.error) {
      throw new Error(`GitHub auth error: ${tokenData.error} — ${tokenData.error_description || ""}`);
    }

    console.log("\n✓ GitHub authentication successful!");
    console.log("🔄  Fetching your GitHub profile...");

    const githubUserRes = await fetch("https://api.github.com/user", {
      headers: { Authorization: `Bearer ${tokenData.access_token}`, Accept: "application/json" },
    });
    if (!githubUserRes.ok) {
      throw new Error(`Could not fetch GitHub profile (${githubUserRes.status}).`);
    }
    const githubUser = await githubUserRes.json();

    const githubEmailRes = await fetch("https://api.github.com/user/emails", {
      headers: { Authorization: `Bearer ${tokenData.access_token}`, Accept: "application/json" },
    });
    if (!githubEmailRes.ok) {
      throw new Error(
        `Could not fetch your GitHub email (${githubEmailRes.status}). ` +
        "Please re-authorize and allow the user:email scope."
      );
    }
    const githubEmails = await githubEmailRes.json();
    if (!Array.isArray(githubEmails)) {
      throw new Error("GitHub returned an invalid email response. Please try logging in again.");
    }
    const primaryEmail = (githubEmails.find(e => e.primary && e.verified) || {}).email
      || (githubEmails.find(e => e.verified) || {}).email;

    if (!primaryEmail) {
      throw new Error("Could not get a verified email from GitHub. Verify an email address, then try again.");
    }

    console.log(`✓ Welcome, ${githubUser.login}!`);
    console.log("🔄  Connecting to Claw-Coder services...");

    const authRes = await fetch(`${getApiUrl()}/auth/github-callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        github_token: tokenData.access_token,
        github_id: String(githubUser.id),
        github_login: githubUser.login,
        avatar_url: githubUser.avatar_url,
      }),
    });
    if (!authRes.ok) {
      const errorText = await authRes.text();
      await logErrorToSupabase(`Server auth failed: ${errorText}`, { endpoint: "github-callback" });
      throw new Error(`Server auth failed: ${errorText}`);
    }
    const supabaseData = await authRes.json();

    // Server currently returns supabase_user_id only; API auth verifies the
    // GitHub token directly. Prefer a real JWT if the server starts returning one.
    const accessToken = (supabaseData && supabaseData.access_token) || tokenData.access_token;
    const expiresAt = (supabaseData && supabaseData.expires_at)
      ? Math.floor(new Date(supabaseData.expires_at).getTime() / 1000)
      : Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;

    const session = {
      access_token:  accessToken,
      refresh_token: (supabaseData && supabaseData.refresh_token) || null,
      expires_at:    expiresAt,
      provider:      "github",
      token_type:    (supabaseData && supabaseData.access_token) ? "supabase" : "github",
      github_token:  tokenData.access_token,
      user: {
        id:    (supabaseData && supabaseData.supabase_user_id) || String(githubUser.id),
        email: primaryEmail,
        user_metadata: {
          user_name:  githubUser.login,
          avatar_url: githubUser.avatar_url,
          github_id:  String(githubUser.id),
        },
      },
    };

    saveSession(session);
    markNewVersionLoginComplete();
    console.log("");
    printBox([
      "Login Successful!",
      `User: ${primaryEmail}`,
      `GitHub: @${githubUser.login}`,
      "Session valid for 30 days (or until GitHub revokes access)",
      "You can now use all Claw-Coder features",
    ]);
    console.log("");
    return session;
  }

  throw new Error("Login timed out — the code expired. Run claw-coder login to try again.");
}

module.exports = { login, loadSession, clearSession, hasCompletedNewVersionLogin, markNewVersionLoginComplete };
