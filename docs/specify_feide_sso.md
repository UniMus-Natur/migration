# Specify + Feide SSO Research Notes

This document captures how to add Feide login to the Specify instance running in this repository's Helm deployment.

## 1) Protocol choice

Feide supports both SAML 2.0 and OpenID Connect (OIDC). Specify's SSO implementation is OIDC-based (`OAUTH_LOGIN_PROVIDERS` in settings), so the correct integration path is **Feide OIDC**.

## 2) What Specify expects for OIDC

Specify loads IdP configuration from `OAUTH_LOGIN_PROVIDERS` in `specify_settings.py` / local overrides.

Provider object shape used by Specify:

- `title`: label shown on login page
- `client_id`
- `client_secret`
- `config`: issuer base URL (Specify appends `/.well-known/openid-configuration`)
- `scope`: requested OIDC scopes

From backend code, the callback endpoint is:

- `/accounts/oic_callback/`

The login flow also supports invite-link association for external identities.

## 3) Feide OIDC technical inputs

Feide customer portal values (verified against live discovery document):

| Setting | Value |
| :--- | :--- |
| Issuer | `https://auth.dataporten.no` |
| Auto configuration | `https://auth.dataporten.no/.well-known/openid-configuration` |
| Authorization endpoint | `https://auth.dataporten.no/oauth/authorization` |
| Token endpoint | `https://auth.dataporten.no/oauth/token` |

For Specify's `config` field, use only the issuer base URL:

- `https://auth.dataporten.no`

Specify fetches the discovery document at `config + '/.well-known/openid-configuration'` and reads the authorization/token endpoints from there — you do **not** need to paste the individual endpoint URLs into Helm values.

The `openid` scope must be authorized on the Feide client (already included in our `scope: openid profile email`).

## 4) Minimum configuration values for this deployment

**Staging** (`specify-stg.natur.unimus.no`) — redirect URI in Feide portal:

- `https://specify-stg.natur.unimus.no/accounts/oic_callback/`

(Legacy doc reference: `https://specify.gbif-no.sigma2.no/accounts/oic_callback/`)

Suggested initial provider config (to be injected into Specify settings):

```python
OAUTH_LOGIN_PROVIDERS = {
    "feide": {
        "title": "Feide",
        "client_id": "<from-feide-portal>",
        "client_secret": "<from-feide-portal>",
        "config": "https://auth.dataporten.no",
        "scope": "openid profile email",
    }
}
```

Notes:

- `openid` is required for OIDC.
- Feide docs indicate `openid` and `userid` are authorized for OIDC configuration; additional claims depend on granted attribute groups/scopes.
- Keep existing local username/password login enabled during rollout.

## 5) Operational rollout plan (recommended)

1. Register service/client in Feide portal and add redirect URI above.
2. Add OIDC provider config to Specify settings override.
3. Deploy to staging first.
4. In Specify UI, generate invite links for test users and perform account-link flow.
5. Validate login, logout, and existing local auth still works.
6. Promote to production after pilot users verify.

## 6) Kubernetes/Helm best-practice for this repo

Use chart-managed settings overrides (mounted files) and keep secrets in Kubernetes `Secret`:

- non-secret app config: provider title, issuer, scopes
- secret data: `client_secret`, possibly `client_id` per policy

Do not hardcode OIDC credentials in the image.

## 7) Helm chart integration (prepared, not yet deployed)

The chart now exposes `specify.oauth.providers` in `values.yaml` / `staging.values.yaml` and renders `OAUTH_LOGIN_PROVIDERS` into `local_specify_settings.py`. Credentials are **not** stored in values — they are read from env vars in `specify-secret`:

| Env var | Purpose |
| :--- | :--- |
| `FEIDE_OIDC_CLIENT_ID` | Feide OIDC client id |
| `FEIDE_OIDC_CLIENT_SECRET` | Feide OIDC client secret |

Non-secret provider settings (title, issuer, scopes) live under `specify.oauth.providers.feide` in Helm values.

### Rollout checklist (do not skip steps)

1. **Feide customer portal** — create OIDC configuration for the service:
   - Redirect URI: `https://specify-stg.natur.unimus.no/accounts/oic_callback/`
   - Scopes: `openid`, `profile`, `email` (add `userid` if your attribute groups require it)
   - Enable Feide test users while validating in staging
2. **Kubernetes secret** — add credentials to `specify-secret` (see `example.env`):
   ```bash
   kubectl patch secret specify-secret -n <namespace> --type merge -p \
     '{"stringData":{"FEIDE_OIDC_CLIENT_ID":"<from-portal>","FEIDE_OIDC_CLIENT_SECRET":"<from-portal>"}}'
   ```
3. **Helm upgrade** — staging values already have `specify.oauth.providers.feide.enabled: true`:
   ```bash
   cd migration/charts/specify7
   helm dependency build
   helm upgrade staging . -f staging.values.yaml -n <namespace>
   ```
4. **Verify** — visit `/accounts/login/` and confirm the Feide button appears; test with a Feide test user and an invite link for account linking.

### Troubleshooting: redirect URI uses `http://` instead of `https://`

If Feide reports a mismatch like `http://…/accounts/oic_callback/` vs the configured `https://…`, TLS is terminating at ingress but Django was building URLs from the plain HTTP request to the pod. The chart sets `SECURE_PROXY_SSL_HEADER` when `ingress.enabled` and `ingress.tls` are both true (requires a Helm upgrade / pod restart to pick up the new config Secret).
5. **User onboarding** — generate invite links for pilot users or bulk-create `spuserexternalid` rows before wider rollout.

## 8) Open questions to resolve with Feide admins

- Exact attribute groups needed by your Specify user-mapping process
- Whether to enable only Feide login provider or also other providers in Feide config
- Whether any organization-level activation steps are required before users can authenticate

## 9) Feide ownership and admin model (who controls the app)

Feide configuration is organization-based in the customer portal, with role-based administration.

Relevant roles:

- `Feide-administrator`: organization-wide admin, can create portal users and assign roles.
- `teneste-administrator` (service administrator): manages one or more specific services.

Implications for this project:

- The Feide OIDC app is not tied to one developer account forever.
- Multiple admins can manage the same service configuration.
- Access continuity is achieved by assigning at least two active admins.

## 10) Development and testing model in Feide

Feide OIDC uses the normal discovery/issuer endpoints, and testing is handled per configuration in the customer portal.

Current practical workflow:

1. Create OIDC configuration for the service in Feide customer portal.
2. Set redirect URI to `https://specify-stg.natur.unimus.no/accounts/oic_callback/` for staging.
3. Enable Feide test users on the configuration while testing.
4. Validate login in staging.
5. Disable test users when not needed and before production usage.

## 10.1) Creating the organization access in Feide (service provider onboarding)

If your organization is not already registered as a Feide service provider, the onboarding path is:

1. Submit Feide service-provider application form.
2. Wait for approval/registration by Feide.
3. Access the Feide customer portal for your organization.
4. Add additional portal users and assign roles (Feide admin/service admin).
5. Create the OIDC configuration for your service and add redirect URI.

Notes:

- Feide documents this as organization-level onboarding, not a per-developer account setup.
- If the organization already exists, an existing Feide administrator can grant you access in portal "Users and roles".

## 11) Preventing "any Feide user can log in"

Yes, this is possible and already aligned with Specify's current login flow.

Specify does not automatically grant application access to any successful OIDC login. Access is controlled by mapping an external identity to an existing Specify user account.

Current behavior in backend flow:

- If the external identity (`provider`, `sub`) is already mapped in `spuserexternalid`, user is logged in.
- If it is not mapped, Specify redirects to legacy login / account-link flow instead of granting access directly.
- Invite links can be generated from UI/API and are permission-gated (`InviteLinkPT.create`), allowing privileged users to onboard users intentionally.
- Important nuance: users who already have valid local Specify credentials can self-link their Feide identity through the legacy-login association flow.

Operationally, this means:

- Feide can authenticate identity.
- Specify still authorizes access based on account linking and internal permissions.
- New users can be controlled via privileged invite-link workflow.
- Existing users with local credentials can self-link unless you additionally restrict or modify the legacy-link path.

