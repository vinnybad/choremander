import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..");
const tokenFile = path.join(repoRoot, "dev", ".token");
const authDir = path.join(here, ".auth");
const statePath = path.join(authDir, "state.json");

/**
 * Log the browser in without touching the login form.
 *
 * The Home Assistant frontend keeps its session in a single localStorage key,
 * `hassTokens`. Seeding that key from the credentials dev/bootstrap.py already
 * captured skips the login UI entirely, which keeps these tests focused on the
 * Choremander cards rather than on Home Assistant's auth flow.
 */
export default async function globalSetup(): Promise<void> {
  if (!fs.existsSync(tokenFile)) {
    throw new Error(
      `Missing ${tokenFile}. Run \`./dev/start.sh\` to bring the environment up ` +
        `before running the browser tests.`,
    );
  }

  // Reset Choremander's stored data before the run. These specs complete chores
  // and spend points against a long-lived container, and daily limits plus
  // un-cancellable reward claims make a second run behave differently from the
  // first unless the fixture data is restored.
  if (process.env.CHOREMANDER_SKIP_SEED !== "1") {
    execFileSync(path.join(repoRoot, "dev", "seed.sh"), {
      cwd: repoRoot,
      stdio: "inherit",
      timeout: 300_000,
    });
  }

  const creds = JSON.parse(fs.readFileSync(tokenFile, "utf8"));
  const baseURL = process.env.HASS_URL ?? creds.hass_url ?? "http://localhost:8123";

  // Fail fast with a clear message rather than letting every spec time out.
  const probe = await fetch(`${baseURL}/api/`, {
    headers: { Authorization: `Bearer ${creds.access_token}` },
  }).catch((err: unknown) => {
    throw new Error(`Cannot reach Home Assistant at ${baseURL}: ${String(err)}`);
  });

  if (!probe.ok) {
    throw new Error(
      `Home Assistant rejected the stored token (HTTP ${probe.status}). ` +
        `Run \`./dev/start.sh\` to refresh dev/.token.`,
    );
  }

  const hassTokens = {
    access_token: creds.access_token,
    token_type: creds.token_type ?? "Bearer",
    refresh_token: creds.refresh_token,
    expires_in: creds.expires_in ?? 1800,
    // The frontend refreshes once this passes; a near-future value is fine
    // because the refresh token is present.
    expires: Date.now() + (creds.expires_in ?? 1800) * 1000,
    hassUrl: baseURL,
    clientId: creds.client_id ?? `${baseURL}/`,
  };

  fs.mkdirSync(authDir, { recursive: true });
  fs.writeFileSync(
    statePath,
    JSON.stringify(
      {
        cookies: [],
        origins: [
          {
            origin: baseURL,
            localStorage: [
              { name: "hassTokens", value: JSON.stringify(hassTokens) },
              // Suppress the "my Home Assistant" onboarding nag that can overlay cards.
              { name: "onboardingDone", value: "true" },
            ],
          },
        ],
      },
      null,
      2,
    ),
  );
}
