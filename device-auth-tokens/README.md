# Device-Bound Push Authentication Tokens

> How do authentication systems push token issuance to a user's device, store a short-lived credential in the client, and require the user to re-acknowledge when that credential expires?

## Status

**Status**: In progress  
**Last updated**: 2026-04-22

## Key Findings

- **OAuth 2.0 Device Authorization Grant (RFC 8628) is the canonical standard for headless push-auth** *(established)* — The device displays a short-lived code; the user approves on a secondary trusted device; an access token (typically 1 hour, AWS SSO: 8 hours) is issued to the client. Silent refresh is possible with a refresh token; without one the user must re-acknowledge.

- **Push MFA (Duo Push, Okta Verify, Microsoft Authenticator push) delivers the strictest user-acknowledgment model** *(established)* — A notification with context (IP, app, location) is pushed to the user's registered device; the user must tap Approve within ~60 seconds. Session tokens last 8–24 hours; no silent background renewal exists — re-approval is mandatory each session lifetime.

- **Tokens stored in secure hardware (iOS Secure Enclave, Android StrongBox, TPM) are non-exportable and device-bound** *(established)* — The signing key never leaves the secure element; even OS-level compromise does not yield a portable secret. User acknowledgment (biometric, PIN, tap) unlocks the key for each assertion.

- **The 1–25 hour access token lifetime is universal across all major identity providers** *(established)* — Microsoft Entra, Okta, and Google default to 1 hour; AWS IAM Identity Center defaults to 8 hours. Refresh token rotation enables silent renewal; withholding the refresh token forces periodic user re-acknowledgment.

- **NIST SP 800-63B-4 mandates re-authentication at 12-hour inactivity / 30-day total for AAL2** *(established)* — Rev. 4 adds §5.3 Session Monitoring for continuous behavioral/device signals that can trigger earlier re-authentication adaptively.

- **Synced passkeys (FIDO2) weaken device-binding guarantees** *(speculative)* — Passkeys synced via iCloud Keychain or Google Password Manager are no longer strictly device-bound; the security model shifts to account-level protection rather than hardware-level binding.

## Open Questions

- How widely deployed is DPoP (RFC 9449) for cryptographically binding OAuth tokens to the requesting device?
- What is the documented UX/security optimum for access token lifetime in push-auth contexts?
- Do any CLI tools combine RFC 8628 with a push MFA notification (eliminating the browser redirect step entirely)?
- How should organizations audit automated API calls that trace back to a human push-auth approval?

## Files

| File | Description |
|------|-------------|
| `findings.md` | Detailed findings across 8 mechanism categories, with evidence, confidence labels, lifetime tables, and open questions |
