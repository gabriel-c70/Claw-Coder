"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const SESSION_DIR = path.join(os.homedir(), ".claw-coder");
const SESSION_FILE = path.join(SESSION_DIR, "session.json");
const NEW_VERSION_LOGIN_FILE = path.join(SESSION_DIR, "new_version_login_complete");

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

    if (data.expires_at && Date.now() / 1000 > data.expires_at - 60) {
      // Session expired, clear only the session file (keep new version login flag)
      if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
      return null;
    }

    const sevenDays = 7 * 24 * 60 * 60;
    if (data.expires_at && (data.expires_at - Date.now() / 1000) < sevenDays) {
      data.expires_at = Math.floor(Date.now() / 1000) + (30 * 24 * 60 * 60);
      saveSession(data);
    }

    return data;
  } catch {
    return null;
  }
}

function clearSession() {
  if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
  if (fs.existsSync(NEW_VERSION_LOGIN_FILE)) fs.unlinkSync(NEW_VERSION_LOGIN_FILE);
}

function hasCompletedNewVersionLogin() {
  return fs.existsSync(NEW_VERSION_LOGIN_FILE);
}

function markNewVersionLoginComplete() {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  fs.writeFileSync(NEW_VERSION_LOGIN_FILE, Date.now().toString(), "utf8");
  try { fs.chmodSync(NEW_VERSION_LOGIN_FILE, 0o600); } catch {}
}

function getApiUrl() {
  return process.env.RATE_LIMIT_API_URL || "https://claw-coder-3.onrender.com";
}
function getDeviceId() {
  const idFile = path.join(SESSION_DIR, "device_id");
  try {
    return fs.readFileSync(idFile, "utf8").trim();
  } catch {
    const id = require("node:crypto").randomUUID();
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

async function login() {
  const { url: supabaseUrl, anonKey, serviceKey, githubClientId } = getSupabaseConfig();

  if (!githubClientId || githubClientId === "your-github-client-id") {
    throw new Error(
      "GITHUB_CLIENT_ID is not set.\n" +
      "Add it to your .env file: GITHUB_CLIENT_ID=your-client-id"
    );
  }

  console.log("\n┌────────────────────────────────────────--─┐");
  console.log("  │         Claw-Coder Login                  │");
  console.log("  ├─────────────────────────────────────────--┤");
  console.log("  │  Authenticating via GitHub OAuth...       │");
  console.log("  │  This works for ANY GitHub account        │");
  console.log("  └─────────────────────────────────────────--┘\n");

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

  console.log("┌─────────────────────────────────────────┐");
  console.log("│  Step 1: Open this link in your browser │");
  console.log("├─────────────────────────────────────────┤");
  console.log(`│  ${device.verification_uri.substring(0, 38).padEnd(38)}│`);
  console.log("├─────────────────────────────────────────┤");
  console.log("│  Step 2: Enter this code                │");
  console.log("├─────────────────────────────────────────┤");
  console.log(`│  ${device.user_code.padEnd(38)}         │`);
  console.log("└─────────────────────────────────────────┘\n");

  const cmd = process.platform === "darwin" ? "open"
             : process.platform === "win32"  ? "start"
             : "xdg-open";
  try {
    require("child_process").execSync(`${cmd} "${device.verification_uri}"`, { stdio: "ignore" });
    console.log("✓ Browser opened automatically\n");
  } catch {
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
      await new Promise(r => setTimeout(r, 3000));
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
    const githubUser = await githubUserRes.json();

    const githubEmailRes = await fetch("https://api.github.com/user/emails", {
      headers: { Authorization: `Bearer ${tokenData.access_token}`, Accept: "application/json" },
    });
    const githubEmails = await githubEmailRes.json();
    const primaryEmail = githubEmails.find(e => e.primary)?.email || githubUser.email;

    if (!primaryEmail) {
      throw new Error("Could not get email from GitHub. Make sure your account has a primary email.");
    }

    console.log(`✓ Welcome, ${githubUser.login}!`);
    console.log("🔄  Connecting to Claw-Coder services...");



    const authRes = await fetch(`${getApiUrl()}/auth/github-callback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            github_token: tokenData.access_token,
            email: primaryEmail,
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

    const accessToken = supabaseData?.access_token || tokenData.access_token;
    const expiresAt = supabaseData?.expires_at
      ? Math.floor(new Date(supabaseData.expires_at).getTime() / 1000)
      : Math.floor(Date.now() / 1000) + (30 * 24 * 60 * 60);

    const session = {
      access_token:  accessToken,
      refresh_token: supabaseData?.refresh_token || null,
      expires_at:    expiresAt,
      provider:      "github",
      github_token:  tokenData.access_token,
      user: {
        id:    supabaseData?.supabase_user_id || String(githubUser.id),
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
    console.log("\n┌────────────────────────────────────────--─┐");
    console.log("  │         Login Successful!                 │");
    console.log("  ├─────────────────────────────────────────--┤");
    console.log(`  │  User: ${primaryEmail.substring(0, 30).padEnd(32)}│`);
    console.log(`  │  GitHub: @${githubUser.login.padEnd(32)}  │`);
    console.log("  ├─────────────────────────────────────────--┤");
    console.log("  │  Session valid for 30 days                │");
    console.log("  │  You can now use all Claw-Coder features  │");
    console.log("  └─────────────────────────────────────────--┘\n");
    return session;
  }

  throw new Error("Login timed out — the code expired. Run claw login to try again.");
}

module.exports = { login, loadSession, clearSession, hasCompletedNewVersionLogin, markNewVersionLoginComplete };