# msauth-gpg-example

A working git credential helper that:

1. Stores your git password in a **GPG-encrypted vault** (`~/.git-auth/credentials.gpg`).
2. Gates vault access with a **Microsoft Entra ID (Azure AD) access token** — default lifetime: **1 hour**.
3. When the token expires, the next `git` operation (push/pull/clone) sends a **push notification to Microsoft Authenticator**. You approve it, a new token is issued, and git proceeds transparently.

No refresh token is persisted. Every renewal requires explicit user acknowledgment.

---

## How the token lifecycle works

```
git push
  └─ git calls: git-auth get
        ├─ token valid?  ──yes──► gpg --decrypt credentials.gpg → output to git
        └─ token expired?
              └─ MSAL device-code flow
                    "Visit https://microsoft.com/devicelogin, enter code: XXXXXX"
                    └─ user opens URL, enters code
                          └─ Azure AD sends push to Microsoft Authenticator
                                └─ user taps Approve
                                      └─ access_token stored (1 h lifetime)
                                            └─ gpg --decrypt → output to git
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.8+ | `python3 --version` |
| GnuPG | `gpg --version`; must have a keypair (`gpg --list-secret-keys`) |
| `msal` Python package | `pip install msal` |
| Microsoft account | Personal (live.com) or work/school Azure AD account |
| Microsoft Authenticator app | Installed and registered on your phone |
| MFA enabled | The Azure AD account must have MFA configured to push to Authenticator |

---

## Step 1 — Register an Azure AD application

> This is a one-time, free operation.  No Azure subscription required.

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**.
2. Name: anything (e.g. `git-auth-helper`). Supported account types: choose the option that matches your account.
3. Redirect URI: leave blank (device flow doesn't need one).
4. Click **Register**.
5. On the overview page, copy:
   - **Application (client) ID** — a UUID like `a1b2c3d4-...`
   - **Directory (tenant) ID** — a UUID like `e5f6g7h8-...`
6. Navigate to **Authentication** → enable **"Allow public client flows"** → Save.
7. Navigate to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated** → `User.Read` → Add.
8. Click **Grant admin consent** (or ask your tenant admin to do so).

---

## Step 2 — Ensure your account has Microsoft Authenticator push MFA

In [mysignins.microsoft.com](https://mysignins.microsoft.com):
- Go to **Security info** → **Add method** → **Authenticator app**.
- Follow the QR-code setup in the Microsoft Authenticator app on your phone.
- Set Authenticator app as the **default sign-in method**.

---

## Step 3 — Install the script

```bash
pip install msal

# Clone or copy this repo, then:
chmod +x /path/to/git-auth
```

---

## Step 4 — Run setup

```bash
./git-auth setup
```

You will be prompted for:

```
Azure AD Application (client) ID: a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Azure AD Directory (tenant) ID:   e5f6g7h8-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GPG recipient (email or key fingerprint): you@example.com

Git credentials to store in the vault:
Protocol [https]: https
Host (e.g. github.com): github.com
Username: your-github-username
Password / Personal Access Token: ****
```

This creates:

```
~/.git-auth/
  config.json          # client_id, tenant_id, gpg_recipient (chmod 600)
  credentials.gpg      # GPG-encrypted git password (chmod 600)
```

No token is stored yet — first use triggers the push.

---

## Step 5 — Register as git credential helper

```bash
# Global (all repos)
git config --global credential.helper '/path/to/git-auth'

# Single repo only
git config credential.helper '/path/to/git-auth'
```

---

## Step 6 — First use

Run any git operation that requires authentication:

```bash
git push
```

Because no token exists, `git-auth` initiates device-code flow and prints to stderr:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code XXXXXX to authenticate.

After entering the code, Azure AD will send a push notification
to your Microsoft Authenticator app — approve it to continue.
```

1. Open the URL on any device.
2. Enter the code.
3. Sign in with your Microsoft account.
4. Azure AD sends a push to Microsoft Authenticator.
5. Tap **Approve** in the app.
6. The CLI receives the access token (1 hour lifetime), decrypts the vault, and git proceeds.

---

## Day-to-day usage

| Scenario | What happens |
|---|---|
| `git push` within the 1-hour window | Token valid → vault decrypts silently → git proceeds |
| `git push` after token expires | Device-code flow starts → push notification sent → user approves → git proceeds |
| `git-auth status` | Shows remaining token lifetime |
| `git-auth unlock` | Force re-authentication (useful for scripting or testing) |
| `git-auth unlock --force` | Re-authenticates even if the current token is still valid |
| `git-auth erase` | Clears the token; next git operation immediately triggers a push |

---

## Token lifetime configuration

The default Azure AD access token lifetime is **3600 seconds (1 hour)**.  You can extend it up to **24 hours** via a [Token Lifetime Policy](https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes):

```powershell
# PowerShell (AzureAD module) — run once in your tenant
$policy = New-AzureADPolicy -Definition @('{"TokenLifetimePolicy":{"Version":1,"AccessTokenLifetime":"08:00:00"}}') `
    -DisplayName "GitAuthHelper8h" -IsOrganizationDefault $false -Type "TokenLifetimePolicy"

# Apply to the app registration
Add-AzureADServicePrincipalPolicy -Id <service-principal-object-id> -RefById $policy.Id
```

This gives you the 8-hour window used by AWS IAM Identity Center, or any value in the 1–24 hour range.

---

## Security properties

| Property | How it's achieved |
|---|---|
| No password in plaintext | GPG public-key encryption; only your private GPG key can decrypt |
| No refresh token stored | `offline_access` scope not requested; MSAL cache not persisted |
| Every renewal requires user action | Device-code flow + push MFA on every token renewal |
| MFA fatigue mitigation | Microsoft Authenticator number-matching (enabled by default in newer tenants) |
| Token not reusable across devices | The GPG vault is local; the token proves auth but vault decryption requires the local GPG key |
| Token not exportable | Token stored only in `~/.git-auth/token.json` (chmod 600) |

---

## File layout

```
~/.git-auth/
  config.json       # Azure AD app config   (chmod 600)
  token.json        # Current access token + expiry (chmod 600, absent when expired)
  credentials.gpg   # GPG-encrypted git credentials (chmod 600)
```

```
git-auth            # This script
requirements.txt    # pip dependencies
```

---

## Troubleshooting

**"msal not found"** — `pip install msal`

**"Not configured"** — run `git-auth setup` first

**"GPG decryption failed"** — ensure `gpg-agent` is running and the private key for the configured recipient is present: `gpg --list-secret-keys`

**No push notification received** — confirm your account has MFA configured and that Microsoft Authenticator is the default sign-in method at [mysignins.microsoft.com](https://mysignins.microsoft.com)

**"AADSTS50034: The user account does not exist"** — ensure the tenant ID matches the account you are signing in with
