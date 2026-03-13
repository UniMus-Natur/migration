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

Feide documentation states:

- OIDC discovery endpoint: `https://auth.dataporten.no/.well-known/openid-configuration`
- Issuer: `https://auth.dataporten.no`
- Client credentials and redirect URIs are managed in Feide customer portal.

For Specify's `config` field, use:

- `https://auth.dataporten.no`

Because Specify resolves discovery using `config + '/.well-known/openid-configuration'`.

## 4) Minimum configuration values for this deployment

For host `https://specify.gbif-no.sigma2.no`, expected redirect URI in Feide portal:

- `https://specify.gbif-no.sigma2.no/accounts/oic_callback/`

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

## 7) Known gap in current chart

Current chart templates configure Django/Specify core settings, but do not yet expose a first-class Helm value for `OAUTH_LOGIN_PROVIDERS`.

Next implementation step should be:

- add `specify.oauthLoginProviders` values key
- render it into `local_specify_settings.py` safely
- optionally split secrets and non-secrets (`ConfigMap` + `Secret`)

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
2. Set redirect URI to `https://specify.gbif-no.sigma2.no/accounts/oic_callback/` for this environment.
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

