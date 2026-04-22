# Findings: Device-Bound Push Authentication Tokens

> Authentication mechanisms that push or prompt authentication to a user's device, where a client-stored token acts on behalf of the user, has a limited lifetime (1–25 hours), and renewal requires user acknowledgment.

**Last updated**: 2026-04-22

---

## Working Implementation

`msauth-gpg-example/git-auth` is a Python CLI that demonstrates the full pattern:

- Git password stored in `~/.git-auth/credentials.gpg` (GPG public-key encrypted)
- Microsoft Entra ID access token (1 hour, no refresh token stored) gates vault access
- On token expiry, MSAL device-code flow triggers → Azure AD sends push to Microsoft Authenticator → user approves → new token issued → vault decrypts
- Registered as a standard `git credential helper`; git calls it automatically on push/pull

See `msauth-gpg-example/README.md` for full setup instructions.

---

## 1. OAuth 2.0 Device Authorization Grant (RFC 8628)

**Claim**: RFC 8628 is the canonical standard for authorizing headless/input-constrained clients by pushing an authentication prompt to a secondary device.
**Evidence**: Defined in [RFC 8628](https://datatracker.ietf.org/doc/html/rfc8628); implemented by GitHub CLI, AWS CLI, Google TV apps, and more.
**Confidence**: *established*

### How it works

1. The client (device) POST-requests a `device_code` and `user_code` from the authorization server.
2. The device displays the `user_code` and a `verification_uri`; the user visits the URI on any browser-capable device.
3. The device polls the token endpoint with `device_code` until the user approves.
4. On approval the server issues an `access_token` (and optionally a `refresh_token`).

### Token storage and binding

- The access token is stored in the client application (device memory or keystore).
- It is not cryptographically bound to the device hardware by the spec itself; binding depends on client implementation.
- Device and user codes expire according to the `expires_in` field on the initial response (configurable by the authorization server; often 5–15 minutes for the code, independent of the access token lifetime).

### Lifetime range

| Token type | Typical lifetime | Notes |
|---|---|---|
| Device/user code | 5–15 minutes | If user does not act, the flow must restart |
| Access token | **1 hour** (most providers) | AWS SSO: **8 hours**; configurable by IdP |
| Refresh token | 30–90 days (or no expiry) | With rotation: single-use |

### Renewal and user acknowledgment

- With a refresh token, the client renews silently — **no user interaction required**.
- Without a refresh token (or after refresh token expiry), the full device authorization flow repeats — **user must re-acknowledge by entering the code and approving on their secondary device**.
- Organizations requiring periodic re-acknowledgment intentionally omit or expire refresh tokens early.

### Security properties

- Phishing-resistant: approval occurs on the user's trusted browser/device, not on the input-constrained device.
- The input-constrained device never handles the user's credentials.

---

## 2. Push MFA / Push Notification Authentication

**Claim**: Push MFA (Duo Push, Okta Verify, Microsoft Authenticator push) issues a device-bound session token after explicit user approval of a push notification.
**Evidence**: Vendor documentation (Duo, Okta, Microsoft Entra MFA); widely deployed in enterprise SSO.
**Confidence**: *established*

### How it works

1. User attempts login; the IdP triggers a push notification to the user's registered authenticator app.
2. The notification contains context (app name, IP, location) so the user can detect fraud.
3. User **taps Approve or Deny** within the expiration window (Duo: ~60 seconds; Okta/Microsoft: similar).
4. On approval, the IdP issues a session token or SAML/OIDC assertion to the client application.

### Token storage and binding

- The cryptographic credential (private key) is stored inside the device's secure hardware:
  - **iOS**: Secure Enclave (P256 ECDSA, non-exportable)
  - **Android**: StrongBox KeyMint / TEE-backed Android Keystore
- Approval signatures are produced within secure hardware; the private key never leaves the device.
- The session token is stored in the browser or client app, not in hardware.

### Lifetime range

| Artifact | Typical lifetime |
|---|---|
| Push notification | 60 seconds (Duo); expires on non-action |
| Session / auth cookie | **8–24 hours** (provider/admin configurable) |

### Renewal and user acknowledgment

- Session renewal: the user re-receives a push notification and must approve again — **explicit user action required every session lifetime**.
- No silent background refresh; users who are inactive longer than the session lifetime must actively re-approve.
- "Remember this device for N days" features exist but are typically disabled in high-security environments.

### Security properties

- Phishing-resistant: push notification arrives on the enrolled device regardless of where the login is attempted.
- MFA fatigue attacks (bombarding users with push requests until they approve) are mitigated by number-matching and context in modern implementations (Duo, Microsoft Authenticator).
- Device binding via secure hardware prevents credential cloning.

---

## 3. FIDO2 / WebAuthn

**Claim**: FIDO2 credentials are cryptographically bound to the authenticator device; session lifetime and re-authentication policy are set by the relying party, not the standard.
**Evidence**: [W3C WebAuthn Level 2 spec](https://www.w3.org/TR/webauthn-2/); Yubico developer guidance on UP vs UV.
**Confidence**: *established*

### How it works

- During **registration** the authenticator generates a credential key pair. The private key never leaves the authenticator (hardware key or platform authenticator like Face ID / Windows Hello).
- During **authentication** the user completes a user presence (UP) gesture (tap) or user verification (UV) gesture (biometric / PIN); the authenticator signs a challenge.
- The relying party (RP) verifies the signature against the stored public key.

### User presence vs. user verification

| Flag | Meaning | User action |
|---|---|---|
| UP | Physical presence | Single tap / button press |
| UV | Identity verified | Biometric / PIN on device |

### Session lifetime and re-authentication

- WebAuthn itself is stateless; the RP issues a session token after successful assertion.
- Typical RP session lifetimes: **1–24 hours**.
- The RP may require a fresh WebAuthn assertion for sensitive operations (step-up authentication), regardless of session age.
- NIST 800-63B: requires re-authentication after at most **12 hours** of inactivity or **30 days** of total session duration at AAL2/AAL3.

### Security properties

- Strongest phishing resistance of any mechanism covered here: credential is origin-bound, so a fake site cannot harvest a usable assertion.
- Hardware binding (Secure Enclave, StrongBox, TPM, YubiKey hardware) makes cloning cryptographically infeasible.

---

## 4. Short-Lived Access Tokens + Refresh Token Rotation (OAuth 2.0 / OIDC)

**Claim**: Short-lived access tokens combined with refresh token rotation provide the most common "1-hour token" pattern; user re-acknowledgment only occurs when refresh tokens are intentionally withheld or expired.
**Evidence**: OAuth 2.0 RFC 6749; OAuth Security BCP RFC 9700; Auth0 and Okta documentation.
**Confidence**: *established*

### Token lifetimes

| Token | Typical lifetime |
|---|---|
| Access token | **1 hour** (industry default); 5–15 min for high-security |
| Refresh token (no rotation) | 30–90 days |
| Refresh token (with rotation) | Single-use; each exchange issues a new one |

### Renewal mechanics

- Silent renewal (no user involvement): client sends `grant_type=refresh_token`; server returns new access token + new refresh token; old refresh token is invalidated.
- **Forced re-authentication**: if the RP issues no refresh token, or sets `offline_access` scope restrictions, the user must re-authenticate when the access token expires.
- Refresh token reuse detection: if a compromised token is reused, the authorization server revokes the entire token family, forcing full re-authentication.

### When user acknowledgment is required

- Full interactive login when: no refresh token exists; refresh token expired; token family revoked; admin-forced sign-out; device compliance failed.
- Step-up auth: sensitive scopes may require re-consent even with a valid session.

### Security properties

- Short lifetimes limit the blast radius of stolen access tokens.
- Rotation + reuse detection prevents indefinite unauthorized access with a captured refresh token.

---

## 5. Hardware-Backed Token Storage

**Claim**: Modern mobile and desktop platforms provide secure hardware enclaves that cryptographically bind key material to specific devices, making token theft or export infeasible.
**Evidence**: Android Keystore documentation; Apple Secure Enclave documentation; TPM 2.0 specification.
**Confidence**: *established*

### Platform comparison

| Platform | Component | Key properties |
|---|---|---|
| iOS (iPhone 5s+, modern iPads/Macs) | Secure Enclave (SEP) | Dedicated coprocessor; P256 ECDSA; non-exportable; ACL-protected (Face ID / Touch ID / passcode) |
| Android (Pie+ with StrongBox) | StrongBox KeyMint / iSE | Dedicated tamper-resistant chip; attestable via Play Integrity API; non-exportable keys |
| Windows / Linux | TPM 2.0 | Discrete or firmware TPM; key bound to device PCR state; non-migratable keys |
| Cross-platform (browsers) | WebAuthn platform authenticator | Uses underlying OS secure element via WebAuthn API |

### Relevance to token lifetime

- The stored private key signs authentication assertions; the assertion (not the key) acts as the short-lived token.
- Even if the operating system is compromised, the key cannot be extracted; an attacker cannot fabricate assertions for an offline key.
- Renewal still requires a user gesture (biometric, PIN, or push notification tap) to unlock the secure element.

---

## 6. SPIFFE/SPIRE and Short-Lived Certificates (mTLS)

**Claim**: SPIFFE/SPIRE issues sub-hour X.509 SVIDs to workloads; rotation is automatic with no user involvement, making it suited to service-to-service (not user-facing) authentication.
**Evidence**: SPIFFE specification; SPIRE documentation; CockroachDB SPIFFE integration article.
**Confidence**: *established*

### How it works

- SPIRE Agent attests the workload's environment (process, container, cloud metadata) and issues an SVID (X.509 certificate or JWT).
- SVIDs typically expire in **1 hour**; SPIRE rotates them automatically before expiry.
- Workloads use SVIDs for mutual TLS (mTLS), where both client and server present certificates.

### Distinction from user-facing mechanisms

- No user acknowledgment: rotation is fully automated.
- Relevant here only as a reference point for 1-hour certificate lifetimes in production systems.
- For user-facing scenarios, a comparable pattern is short-lived client certificates issued after FIDO2 or push MFA — the user authenticates once, and the certificate carries the session for 1–8 hours.

---

## 7. Continuous Authentication and NIST 800-63B Re-authentication Policy

**Claim**: NIST SP 800-63B-4 requires periodic re-authentication and introduces session monitoring; organizations implementing these policies force user re-acknowledgment on configurable schedules.
**Evidence**: [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html); TypingDNA blog analysis of §5.3.
**Confidence**: *established*

### NIST re-authentication intervals by AAL

| AAL | Max session without re-auth | Notes |
|---|---|---|
| AAL1 | 30 days | Low-assurance; single factor |
| AAL2 | 12 hours inactivity / 30 days total | Requires 2nd factor; common enterprise setting |
| AAL3 | Same as AAL2 | Hardware-backed; highest assurance |

### Session monitoring (§5.3, introduced in Rev. 4)

- Allows continuous behavioral and device signals to trigger re-authentication dynamically.
- Signals: typing cadence, mouse patterns, geolocation change, device hardware change, IP reputation.
- Response: session termination, silent step-up prompt, or security notification.

### Real-world configurable defaults

| Provider | Default access token / session lifetime | Configurable range |
|---|---|---|
| Microsoft Entra ID | 1 hour (access token); 90 days rolling (sign-in session) | 5 minutes–1 day (access token); hours–days (session) |
| Okta | 1 hour | Minutes–24 hours |
| AWS IAM Identity Center | **8 hours** | 1–90 days (session); 1 hour or less recommended for access token |
| Google Workspace | 1 hour | Admin-configurable |

---

## 8. Notable Real-World Implementations

### GitHub Device Flow (CLI / headless apps)

- Implements RFC 8628 for `gh auth login`.
- User code expires if not acknowledged within ~15 minutes.
- For GitHub Apps with token expiration enabled: access tokens valid **8 hours**; refresh tokens valid **6 months** (single-use with rotation).
- User must re-run `gh auth login` when both tokens expire.
- Source: [GitHub Docs - Authorizing OAuth Apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)

### AWS IAM Identity Center (CLI / SDK)

- Device code expires after **600 seconds** (10 minutes); access token valid **8 hours**.
- User authenticates in browser; CLI polls for token.
- After 8 hours, the full device flow repeats — user must re-acknowledge in browser.
- Source: AWS CLI documentation; [Anatomy of AWS SSO Device Authorization Grant](https://medium.com/@lex.berger/anatomy-of-aws-sso-device-authorization-grant-2839008c367a)

### Google OAuth (TV / installed apps)

- Device code expires per `expires_in` in the initial response (Google default: 1800 seconds / 30 minutes).
- Access tokens: **1 hour**; refresh tokens: long-lived (configurable expiry, or revocable by user at any time from myaccount.google.com).
- User re-acknowledgment occurs only when the refresh token is revoked or expires.
- Source: [Google OAuth 2.0 for TV and Limited-Input Devices](https://developers.google.com/identity/protocols/oauth2/limited-input-device)

### Duo Push / Okta Verify (Enterprise MFA)

- Session tokens: **8–24 hours** (admin-configurable); push notification approval window: ~60 seconds.
- Number-matching (Duo, Microsoft Authenticator): user must type a 2-digit code shown on the login page into the app, preventing MFA fatigue attacks.
- After session expiry, a new push notification is sent; user must actively approve again.

---

## Cross-Cutting Patterns

1. **Device binding is hardware-enforced** in modern implementations (Secure Enclave, StrongBox, TPM, YubiKey). Keys never leave secure hardware; compromise of the OS does not yield exportable secrets.

2. **The 1–25 hour window is the dominant access token lifetime** across all major identity providers. AWS SSO sits at the high end (8 hours); security-sensitive APIs target the low end (15–60 minutes).

3. **User acknowledgment is decoupled from token use**. The user acknowledges once (via push tap, device code entry, or biometric) and the token then operates silently on their behalf. Re-acknowledgment is required only when the token (or its refresh token) expires.

4. **Silent renewal vs. forced re-authentication is a policy choice**, not a technical constraint. Providing no refresh token — or expiring it aggressively — forces the user back through the acknowledgment flow.

5. **Push MFA is the primary "prompt to device" mechanism** for web-app scenarios. Device authorization grant (RFC 8628) serves headless/CLI scenarios. FIDO2 serves browser-based high-assurance scenarios.

6. **Number-matching and context in push notifications** have become standard defenses against MFA fatigue (push spam) attacks.

---

## Open Questions

1. Are there emerging standards for binding OAuth access tokens cryptographically to the device that issued them (e.g., DPoP — Demonstrating Proof of Possession, RFC 9449)? How widely is DPoP deployed in push-auth contexts?

2. What is the security and UX impact of very short token lifetimes (< 1 hour) when combined with mandatory re-acknowledgment? Is there a documented optimal range?

3. How do passkeys (synced FIDO2 credentials) affect device binding guarantees? Synced passkeys are no longer device-bound in the traditional sense.

4. Are there hybrid mechanisms that combine push MFA acknowledgment with RFC 8628 device flow for CLI tools (i.e., the CLI triggers a push notification rather than a browser redirect)?

5. What are the auditability requirements when a user-acknowledged token is used for automated or batch operations? How do organizations handle the chain of custody between the human approval and the downstream API calls?
