"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const SESSION_DIR = path.join(os.homedir(), ".claw-coder");
const SESSION_FILE = path.join(SESSION_DIR, "session.json");

// ── Baked-in config — users need zero setup ───────────────────────────────────
const BAKED_CONFIG = {
  supabaseUrl:    "https://yourref.supabase.co",
  anonKey:        "your-anon-key",
  githubClientId: "your-github-client-id",
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
    serviceKey:     process.env.SUPABASE_SERVICE_KEY || null,
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

    // fully expired — force re-login
    if (data.expires_at && Date.now() / 1000 > data.expires_at - 60) {
      return null;
    }

    // silently extend if less than 7 days remaining
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
}

async function upsertSupabaseUser(supabaseUrl, serviceKey, email, githubId, githubLogin, avatarUrl) {
  if (!serviceKey) return null;

  const listRes = await fetch(
    `${supabaseUrl}/auth/v1/admin/users?email=${encodeURIComponent(email)}`,
    {
      headers: {
        "apikey": serviceKey,
        "Authorization": `Bearer ${serviceKey}`,
      },
    }
  );

  if (listRes.ok) {
    const listData = await listRes.json();
    const existing = listData.users?.find(u => u.email === email);
    if (existing) {
      const signInRes = await fetch(
        `${supabaseUrl}/auth/v1/admin/users/${existing.id}/session`,
        {
          method: "POST",
          headers: {
            "apikey": serviceKey,
            "Authorization": `Bearer ${serviceKey}`,
            "Content-Type": "application/json",
          },
        }
      );
      if (signInRes.ok) {
        const signInData = await signInRes.json();
        return {
          supabase_user_id: existing.id,
          access_token: signInData.access_token,
          refresh_token: signInData.refresh_token,
          expires_at: signInData.expires_at,
        };
      }
      return { supabase_user_id: existing.id };
    }
  }

  const createRes = await fetch(`${supabaseUrl}/auth/v1/admin/users`, {
    method: "POST",
    headers: {
      "apikey": serviceKey,
      "Authorization": `Bearer ${serviceKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      email,
      email_confirm: true,
      user_metadata: {
        github_id: githubId,
        user_name: githubLogin,
        avatar_url: avatarUrl,
        provider: "github",
      },
      app_metadata: {
        provider: "github",
        providers: ["github"],
      },
    }),
  });

  if (!createRes.ok) {
    const err = await createRes.text();
    console.warn(`Warning: could not create Supabase user: ${err}`);
    return null;
  }

  const newUser = await createRes.json();

  const sessionRes = await fetch(
    `${supabaseUrl}/auth/v1/admin/users/${newUser.id}/session`,
    {
      method: "POST",
      headers: {
        "apikey": serviceKey,
        "Authorization": `Bearer ${serviceKey}`,
        "Content-Type": "application/json",
      },
    }
  );

  if (sessionRes.ok) {
    const sessionData = await sessionRes.json();
    return {
      supabase_user_id: newUser.id,
      access_token: sessionData.access_token,
      refresh_token: sessionData.refresh_token,
      expires_at: sessionData.expires_at,
    };
  }

  return { supabase_user_id: newUser.id };
}

async function login() {
  const { url: supabaseUrl, anonKey, serviceKey, githubClientId } = getSupabaseConfig();

  if (!githubClientId || githubClientId === "your-github-client-id") {
    throw new Error(
      "GITHUB_CLIENT_ID is not set.\n" +
      "Add it to your .env file: GITHUB_CLIENT_ID=your-client-id"
    );
  }

  // Step 1 — request device code from GitHub
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

  // Step 2 — show user the code
  console.log("\n┌─────────────────────────────────────────┐");
  console.log("│         Claw-Coder Login                │");
  console.log("├─────────────────────────────────────────┤");
  console.log(`│  Open:  ${device.verification_uri.padEnd(32)}│`);
  console.log(`│  Code:  ${device.user_code.padEnd(32)}│`);
  console.log("└─────────────────────────────────────────┘\n");

  const cmd = process.platform === "darwin" ? "open"
             : process.platform === "win32"  ? "start"
             : "xdg-open";
  try {
    require("child_process").execSync(`${cmd} "${device.verification_uri}"`, { stdio: "ignore" });
  } catch {}

  // Step 3 — poll GitHub until user approves
  console.log("Waiting for you to approve in the browser...\n");
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

    if (tokenData.error === "authorization_pending") continue;
    if (tokenData.error === "slow_down") {
      await new Promise(r => setTimeout(r, 3000));
      continue;
    }
    if (tokenData.error) {
      throw new Error(`GitHub auth error: ${tokenData.error} — ${tokenData.error_description || ""}`);
    }

    // Step 4 — get user info from GitHub
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

    // Step 5 — upsert user into Supabase
    console.log("Connecting to Supabase...");
    const supabaseData = await upsertSupabaseUser(
      supabaseUrl, serviceKey, primaryEmail,
      String(githubUser.id), githubUser.login, githubUser.avatar_url,
    );

    // Step 6 — build session
    // NOTE: if supabaseData has no access_token, fall back to GitHub token
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
    console.log(`\n✓ Logged in as ${primaryEmail}`);
    console.log(`  Session valid for 30 days — you won't need to login again.\n`);
    return session;
  }

  throw new Error("Login timed out — the code expired. Run claw login to try again.");
}

module.exports = { login, loadSession, clearSession };