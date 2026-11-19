// Non-interactive Ring token issuance for Lookout.
//
// This is the programmatic counterpart of `ring-auth-cli`: it uses the exact
// same ring-client-api auth flow the interactive CLI wraps internally, but it
// never opens a PTY, prints a prompt, or scrapes output. Credentials are read
// only from the environment (never from argv) and the wrapped refresh token is
// the sole value written to stdout. No credential or token value is logged.
//
// Environment variables (all required, injected by RingAuthCliIssuer):
//   LOOKOUT_RING_EMAIL     - Ring account email
//   LOOKOUT_RING_PASSWORD  - Ring account password
//   LOOKOUT_RING_2FA_CODE  - TOTP or SMS 2FA code (both use the same API header)

import { RingRestClient } from 'ring-client-api/lib/rest-client.js';

const email = process.env.LOOKOUT_RING_EMAIL;
const password = process.env.LOOKOUT_RING_PASSWORD;
const code = process.env.LOOKOUT_RING_2FA_CODE;

async function issueToken() {
  if (!email || !password) {
    throw new Error('Missing Ring credentials');
  }

  const client = new RingRestClient({ email, password });

  let auth = null;
  try {
    // Returns already-issued auth when the account has no pending 2FA challenge.
    auth = await client.getCurrentAuth();
  } catch {
    // 2FA is required (or there is no cached auth); fall through to getAuth.
  }

  if (!auth) {
    if (!code) {
      throw new Error('Two-factor code required');
    }
    // Same `2fa-code` header path the CLI uses for both TOTP and SMS codes.
    auth = await client.getAuth(code);
  }

  const refreshToken = auth && auth.refresh_token;
  if (typeof refreshToken !== 'string' || refreshToken.length === 0) {
    throw new Error('No refresh token returned');
  }
  process.stdout.write(refreshToken);
}

// Never surface an error message that could contain a credential or token.
issueToken().catch(() => {
  process.stderr.write('Ring authentication failed\n');
  process.exit(1);
});
