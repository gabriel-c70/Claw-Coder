"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const SESSION_DIR = path.join(os.homedir(), ".claw-coder");
const SESSION_FILE = path.join(SESSION_DIR, "session.json");

function getSupabaseConfig() {
  const envFile = path.join(path.resolve(__dirname, ".."), ".env");
  if (fs.existsSync(envFile)) {
    for (const line of fs.readFileSync(envFile, "utf8").split("\n")) {
      const [key, ...rest] = line.split("=");
      if (key && rest.length && !process.env[key.trim()]) {
        process.env[key.trim()] = rest.join("=").trim();
      }
    }
  }
  const url = process.env.SUPABASE_URL;
  const anonKey = process.env.SUPABASE_ANON_KEY;
  const serviceKey = process.env.SUPABASE_SERVICE_KEY;
  const githubClientId = process.env.GITHUB_CLIENT_ID;

  if (!url || !anonKey) {
    throw new Error("Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env");
  }
  if (!githubClientId) {
    throw new Error("Missing GITHUB_CLIENT_ID in .env");
  }
  return { url, anonKey, serviceKey, githubClientId };
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
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

function clearSession() {
  if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
}

// ── Upsert user into Supabase using admin API ─────────────────────────────────
// This creates the user in Supabase if they don't exist,
// or returns their existing account if they do.
async function upsertSupabaseUser(supabaseUrl, serviceKey, email, githubId, githubLogin, avatarUrl) {
  if (!serviceKey) {
    // no service key — skip Supabase upsert, just use GitHub session
    return null;
  }

  // check if user already exists in Supabase by email
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
      // user exists — create a session token for them
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
      // if session creation fails just return the user id
      return { supabase_user_id: existing.id };
    }
  }

  // user doesn't exist — create them
  const createRes = await fetch(`${supabaseUrl}/auth/v1/admin/users`, {
    method: "POST",
    headers: {
      "apikey": serviceKey,
      "Authorization": `Bearer ${serviceKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      email: email,
      email_confirm: true,          // skip email confirmation
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

  // create a session for the new user
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

// ── Main login function ───────────────────────────────────────────────────────
async function login() {
  const { url: supabaseUrl, anonKey, serviceKey, githubClientId } = getSupabaseConfig();

  // Step 1 — request device code from GitHub
  const deviceRes = await fetch("https://github.com/login/device/code", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
    body: JSON.stringify({
      client_id: githubClientId,
      scope: "read:user user:email",
    }),
  });
  const device = await deviceRes.json();

  // check GitHub didn't return an error
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

  // auto open browser
  const cmd = process.platform === "darwin" ? "open"
             : process.platform === "win32"  ? "start"
             : "xdg-open";
  try {
    require("child_process").execSync(
      `${cmd} "${device.verification_uri}"`,
      { stdio: "ignore" }
    );
  } catch {}

  // Step 3 — poll GitHub until user approves
  console.log("Waiting for you to approve in the browser...\n");
  const pollInterval = (device.interval || 5) * 1000;
  const expires = Date.now() + device.expires_in * 1000;

  while (Date.now() < expires) {
    await new Promise(r => setTimeout(r, pollInterval));

    const tokenRes = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
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
      headers: {
        Authorization: `Bearer ${tokenData.access_token}`,
        Accept: "application/json",
      },
    });
    const githubUser = await githubUserRes.json();

    const githubEmailRes = await fetch("https://api.github.com/user/emails", {
      headers: {
        Authorization: `Bearer ${tokenData.access_token}`,
        Accept: "application/json",
      },
    });
    const githubEmails = await githubEmailRes.json();
    const primaryEmail = githubEmails.find(e => e.primary)?.email || githubUser.email;

    if (!primaryEmail) {
      throw new Error(
        "Could not get email from GitHub. Make sure your GitHub account has a public or primary email."
      );
    }

    // Step 5 — upsert user into Supabase
    // This sends the GitHub user data to Supabase and creates
    // or retrieves their Supabase account
    console.log("Connecting to Supabase...");
    const supabaseData = await upsertSupabaseUser(
      supabaseUrl,
      serviceKey,
      primaryEmail,
      String(githubUser.id),
      githubUser.login,
      githubUser.avatar_url,
    );

    // Step 6 — build and save session
    const session = {
      // use Supabase token if we got one, otherwise GitHub token
      access_token: supabaseData?.access_token || tokenData.access_token,
      refresh_token: supabaseData?.refresh_token || null,
      expires_at: supabaseData?.expires_at
        ? Math.floor(new Date(supabaseData.expires_at).getTime() / 1000)
        : Math.floor(Date.now() / 1000) + (8 * 60 * 60),
      provider: "github",
      github_token: tokenData.access_token,   // always keep GitHub token too
      user: {
        id: supabaseData?.supabase_user_id || String(githubUser.id),
        email: primaryEmail,
        user_metadata: {
          user_name: githubUser.login,
          avatar_url: githubUser.avatar_url,
          github_id: String(githubUser.id),
        },
      },
    };

    saveSession(session);

    if (supabaseData?.supabase_user_id) {
      console.log(`\n✓ Logged in as ${primaryEmail}`);
      console.log(`  Supabase user ID: ${supabaseData.supabase_user_id}`);
    } else {
      console.log(`\n✓ Logged in as ${primaryEmail}`);
      console.log(`  (Add SUPABASE_SERVICE_KEY to .env to sync user to Supabase)`);
    }

    return session;
  }

  throw new Error("Login timed out — the code expired. Run claw login to try again.");
}

module.exports = { login, loadSession, clearSession };