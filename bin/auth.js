"use strict";

const http = require("node:http");
const crypto = require("node:crypto");
const { execSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const SESSION_DIR = path.join(os.homedir(), ".claw-coder");
const SESSION_FILE = path.join(SESSION_DIR, "session.json");
const CALLBACK_PORT = 54321;

function getSupabaseConfig() {
  // load .env from package root
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
  if (!url || !anonKey) {
    throw new Error(
      "Missing SUPABASE_URL or SUPABASE_ANON_KEY in your .env file.\n" +
      "See: https://supabase.com/dashboard → Project Settings → API"
    );
  }
  return { url, anonKey };
}

function generatePKCE() {
  const verifier = crypto.randomBytes(32).toString("base64url");
  const challenge = crypto.createHash("sha256").update(verifier).digest("base64url");
  return { verifier, challenge };
}

function saveSession(session) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  fs.writeFileSync(SESSION_FILE, JSON.stringify(session, null, 2), "utf8");
  try { fs.chmodSync(SESSION_FILE, 0o600); } catch {}  // unix only
}

function loadSession() {
  if (!fs.existsSync(SESSION_FILE)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(SESSION_FILE, "utf8"));
    // expires_at is a unix timestamp in seconds from Supabase
    if (data.expires_at && Date.now() / 1000 > data.expires_at - 60) {
      return null;  // treat as expired 1 min early
    }
    return data;
  } catch {
    return null;
  }
}

function clearSession() {
  if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
}

async function exchangeCode(code, verifier, supabaseUrl, anonKey) {
  const res = await fetch(`${supabaseUrl}/auth/v1/token?grant_type=pkce`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: anonKey },
    body: JSON.stringify({ auth_code: code, code_verifier: verifier }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Token exchange failed: ${text}`);
  }
  return res.json();
}

function openBrowser(url) {
  const cmd = process.platform === "darwin" ? "open"
            : process.platform === "win32"  ? "start"
            : "xdg-open";
  try {
    execSync(`${cmd} "${url}"`, { stdio: "ignore" });
  } catch {
    console.log(`\nCould not open browser automatically.\nOpen this URL manually:\n${url}\n`);
  }
}

async function login(provider = "github") {
  const { url: supabaseUrl, anonKey } = getSupabaseConfig();
  const { verifier, challenge } = generatePKCE();
  const state = crypto.randomBytes(16).toString("hex");

  const authUrl = new URL(`${supabaseUrl}/auth/v1/authorize`);
  authUrl.searchParams.set("provider", provider);
  authUrl.searchParams.set("redirect_to", `http://localhost:${CALLBACK_PORT}/callback`);
  authUrl.searchParams.set("code_challenge", challenge);
  authUrl.searchParams.set("code_challenge_method", "S256");
  authUrl.searchParams.set("state", state);

  return new Promise((resolve, reject) => {
    const server = http.createServer(async (req, res) => {
      const reqUrl = new URL(req.url, `http://localhost:${CALLBACK_PORT}`);
      if (reqUrl.pathname !== "/callback") { res.end(); return; }

      const code  = reqUrl.searchParams.get("code");
      const retState = reqUrl.searchParams.get("state");
      const error = reqUrl.searchParams.get("error");

      const html = (title, body) =>
        `<html><body style="font-family:sans-serif;max-width:420px;margin:80px auto;text-align:center">
          <h2>${title}</h2>${body}
          <p style="color:#888;margin-top:32px">You can close this tab.</p>
        </body></html>`;

      if (error) {
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(html("Login failed", `<p>${error}</p>`));
        server.close();
        reject(new Error(`OAuth error: ${error}`));
        return;
      }
      if (retState !== state) {
        res.writeHead(400, { "Content-Type": "text/html" });
        res.end(html("State mismatch", "<p>Possible CSRF. Try again.</p>"));
        server.close();
        reject(new Error("State mismatch — possible CSRF"));
        return;
      }
      try {
        const session = await exchangeCode(code, verifier, supabaseUrl, anonKey);
        saveSession(session);
        const email = session.user?.email || "user";
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(html("✅ Logged in to Claw-Coder",
          `<p>Welcome, <strong>${email}</strong>!</p>
           <p>Return to your terminal.</p>`));
        server.close();
        resolve(session);
      } catch (err) {
        res.writeHead(500, { "Content-Type": "text/html" });
        res.end(html("Error", `<p>${err.message}</p>`));
        server.close();
        reject(err);
      }
    });

    server.listen(CALLBACK_PORT, () => {
      console.log(`\nOpening browser for ${provider} login...`);
      openBrowser(authUrl.toString());
      console.log("Waiting for login... (times out in 5 minutes)\n");
    });

    server.on("error", (err) => {
      if (err.code === "EADDRINUSE") {
        reject(new Error(`Port ${CALLBACK_PORT} is already in use. Kill it and retry.`));
      } else {
        reject(err);
      }
    });

    setTimeout(() => {
      server.close();
      reject(new Error("Login timed out after 5 minutes."));
    }, 5 * 60 * 1000);
  });
}

module.exports = { login, loadSession, clearSession };