# Authentication Guide

## Admin Bootstrap

The stack ships with a single **seed** admin credential (`admin`/`admin` by default). Treat it as a one-time key to open the door — not a permanent account.

On first successful login, the dashboard forces a password rotation. After rotation, the seed value is no longer accepted; the persisted credential in `users.json` is authoritative.

Override the seed value before first boot via `STACK_ADMIN_USERNAME` / `STACK_ADMIN_PASSWORD` (Compose: `.env`; K8s: `media-stack-secrets`). Once rotation has occurred, changing these env vars has no effect.

### Break-glass recovery (lost admin password)

Use the `reset-admin` CLI from inside the controller pod. It writes the new
hash into Authelia's `users_database.yml`, upserts the controller's own
`users.json` row with `source=rotated`, and audit-logs the reset — no
restart or file surgery required.

```bash
# K8s
kubectl -n media-stack exec -it deploy/media-stack-controller -- \
    bin/reset-admin.sh --username admin --prompt

# Compose
docker exec -it media-stack-controller \
    bin/reset-admin.sh --username admin --prompt
```

Flags: `--password <literal>` (unsafe in shared shells), `--prompt`
(no-echo TTY prompt), or `--password-stdin` (read from stdin, pair
with a secret manager).

If the controller pod itself won't start, fall back to the manual path:
stop the controller, delete `${CONFIG_ROOT}/controller/users.json`,
restart. The seed credential becomes valid again for one login.

## Auth Modes

| Mode | Gateway Auth | Controller Auth | Use Case |
|---|---|---|---|
| `none` | — | — | Trusted LAN, no login required |
| `basic` | — | HTTP Basic Auth | LAN with minimal security |
| `authelia` | Authelia ext_authz | Forwarded identity | SSO via Authelia (lightweight) |
| `authentik` | Authentik ext_authz | Forwarded identity | SSO via Authentik (full IdP) |

Set the mode in your bootstrap profile:

```yaml
auth:
  provider: authelia
  mode: authelia
  enabled: true
```

Or change it live from the dashboard **Settings → Auth** tab.

## Authelia with OIDC (Google, GitHub, etc.)

Authelia acts as the **local forward-auth proxy** in front of Envoy. It can authenticate users with its own file-based user database, or delegate to an external OIDC identity provider (Google, Auth0, Okta, GitHub, Microsoft Entra, Keycloak, or any custom OIDC provider).

### Supported OIDC Providers

| Provider | Discovery | Required Fields |
|---|---|---|
| Local Accounts | — (file-based) | — |
| Google | `accounts.google.com` | `client_id`, `client_secret` |
| Auth0 | `{tenant}.auth0.com` | `tenant`, `client_id`, `client_secret` |
| Okta | `{domain}/oauth2/default` | `domain`, `client_id`, `client_secret` |
| Microsoft Entra | `login.microsoftonline.com/{tenant_id}` | `tenant_id`, `client_id`, `client_secret` |
| GitHub | OAuth endpoints (limited OIDC) | `client_id`, `client_secret` |
| Keycloak | `{host}/realms/{realm}` | `host`, `realm`, `client_id`, `client_secret` |
| Custom | Any OIDC provider | `discovery_url`, `client_id`, `client_secret` |

### Profile Configuration (Google Example)

```yaml
auth:
  provider: authelia
  mode: authelia
  enabled: true
  oidc_provider: google
  oidc_config:
    client_id: "123456789-abc.apps.googleusercontent.com"
    client_secret: "GOCSPX-your-secret-here"
  per_service:
    authelia: public       # auth provider itself — no ext_authz
    jellyfin: native       # TV/mobile apps can't do OIDC redirects
    jellyseerr: protected  # web-only UI — SSO works fine
```

## TLS and Envoy Gateway

Envoy terminates TLS on port **443** with a self-signed certificate auto-minted on first boot (see `_resolve_or_mint_certs` in the compose dynamic-config generator). Port **80** redirects to 443. The legacy port **8880** is plain HTTP and intended only for local probe/testing — browsers should use `https://...`.

Authelia 4.38+ requires HTTPS for session cookies. The stack's `session.cookies[].authelia_url` is always emitted as an `https://` URL that shares cookie scope with `apps.<domain>` (same registrable domain).

## Domain Topology (Nested vs Flat)

The stack supports two domain layouts. The generator picks automatically based on your profile.

| Layout | Base | Sub | Gateway | Authelia |
|---|---|---|---|---|
| Nested (Compose default) | `media-stack.local` | `media-stack` | `apps.media-stack.local` | `auth.media-stack.local` |
| Flat (K8s default) | `iomio.io` | *(empty)* | `m.iomio.io` | `auth.iomio.io` |

When `routing.base_domain` is set on the profile and the gateway hostname sits directly under it, the stack uses the flat form. Mixing the two (e.g. `auth.media-stack.media-stack.local`) breaks cookie scope and produces an infinite login-redirect loop — the configuration generator guards against this.

## DNS Setup

Authelia needs browser-resolvable hostnames. The stack uses three domain patterns and you need DNS entries for all of them.

### Required DNS Records

Point all entries to your Docker host IP (the machine running the stack):

```
# Replace 192.168.1.100 with your Docker host IP

# Gateway (path-prefix routing)
192.168.1.100  apps.media-stack.local

# Auth provider
192.168.1.100  auth.media-stack.local

# Per-service subdomains (if using hybrid or subdomain routing)
192.168.1.100  jellyfin.media-stack.local
192.168.1.100  sonarr.media-stack.local
192.168.1.100  radarr.media-stack.local
192.168.1.100  prowlarr.media-stack.local
192.168.1.100  jellyseerr.media-stack.local
192.168.1.100  qbittorrent.media-stack.local
192.168.1.100  homepage.media-stack.local
# ... one per enabled service
```

### Option A: /etc/hosts (Single Machine)

Add the entries above to `/etc/hosts` on the machine where you open the browser:

```bash
sudo nano /etc/hosts
```

### Option B: dnsmasq / AdGuard / Pi-hole (Whole Network)

Use a wildcard so all `*.media-stack.local` subdomains resolve automatically:

```
# dnsmasq
address=/media-stack.local/192.168.1.100

# AdGuard DNS rewrite
*.media-stack.local → 192.168.1.100
```

### Option C: EdgeRouter / Router DNS

Add a static host mapping for `media-stack.local` pointing to your Docker host IP.

### Generate DNS Entries Automatically

The stack includes helper scripts:

```bash
# /etc/hosts format
bash bin/render-hosts-example.sh 192.168.1.100 media-stack

# dnsmasq snippet
bash bin/render-dnsmasq-snippet.sh 192.168.1.100 media-stack
```

## URL Reference

When Authelia is active, these URLs are in play:

| URL | Purpose |
|---|---|
| `https://apps.media-stack.local/` | Gateway entry point (Envoy, TLS) |
| `https://auth.media-stack.local/` | Authelia login portal |
| `https://auth.media-stack.local/api/authz/forward-auth` | Envoy ext_authz verification endpoint |
| `https://auth.media-stack.local/api/oidc/callback` | OIDC redirect callback (registered at IdP) |
| `https://auth.media-stack.local/api/authz/logout` | Logout endpoint |

> URLs are rendered with `https://` and the Envoy HTTPS port (default 443). The legacy HTTP port `8880` is still accepted for local testing but Authelia 4.38+ sessions only work over HTTPS.

## Registering the OIDC Redirect URI

When using an external IdP (Google, Auth0, etc.), you must register the **redirect URI** at the IdP so it knows where to send users after login.

### Google Cloud Console

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Select your OAuth 2.0 Client ID (or create one)
3. Under **Authorized redirect URIs**, add:
   ```
   https://auth.media-stack.local/api/oidc/callback
   ```
4. Under **Authorized JavaScript origins**, add:
   ```
   https://auth.media-stack.local
   ```
5. Save

> Google does not require the redirect URI to be publicly reachable. The redirect happens in the browser, which is on your LAN. Your machine just needs internet access to reach Google's token endpoint.

### Auth0

1. Go to your Auth0 Dashboard → Applications → your app
2. Under **Allowed Callback URLs**, add:
   ```
   https://auth.media-stack.local/api/oidc/callback
   ```
3. Under **Allowed Logout URLs**, add:
   ```
   https://apps.media-stack.local/
   ```

### Other Providers

The pattern is the same for all providers — register `https://auth.media-stack.local/api/oidc/callback` as the allowed redirect/callback URI in your IdP's application settings.

## Testing Locally (No Public Internet Required)

Authelia works on a LAN without any internet exposure. The OIDC flow works because:

1. **Browser redirects are local** — the browser on your LAN follows the redirect to `auth.media-stack.local`, which resolves to your Docker host
2. **Token exchange is server-side** — Authelia contacts the IdP's token endpoint directly (needs outbound internet, not inbound)
3. **No public IP or port forwarding needed**

### Step-by-Step Local Test

**1. Set DNS** — ensure `auth.media-stack.local` and `apps.media-stack.local` resolve to your Docker host (see DNS Setup above).

**2. Set auth mode** — either edit the profile YAML or use the dashboard:

```yaml
auth:
  provider: authelia
  mode: authelia
  enabled: true
  oidc_provider: local    # use file-based users for offline testing
```

**3. Create a local user** — hash a password for the Authelia users database:

```bash
docker run --rm authelia/authelia:latest \
  authelia crypto hash generate argon2 --password 'your-test-password'
```

Put the hash in `${CONFIG_ROOT}/authelia/users_database.yml`:

```yaml
users:
  admin:
    disabled: false
    displayname: "Media Stack Admin"
    email: "admin@local"
    groups:
      - admins
    password: "$argon2id$v=19$m=65536,t=3,p=4$..."  # paste hash here
```

**4. Start the stack** — Authelia starts on port 9091 internally, Envoy proxies it:

```bash
./deploy-compose.sh
```

**5. Open the dashboard** — navigate to `https://apps.media-stack.local/` (accept the self-signed cert on first load)

- Envoy sends the request to Authelia for verification via ext_authz
- Authelia redirects unauthenticated users to the login portal
- After login, Authelia sets a session cookie and redirects back
- The dashboard header shows your username to confirm you're authenticated

**6. Verify identity** — check the user badge in the top-right corner of the dashboard. It shows the authenticated username. You can also call the API directly:

```bash
curl -sk https://apps.media-stack.local/api/auth/identity \
  -H "Cookie: authelia_session=<your-cookie>"
```

### Testing with an External IdP (Google)

Same as above, but set `oidc_provider: google` and provide your OAuth credentials. The only additional requirement is that your machine has outbound internet access so Authelia can reach Google's token endpoint.

### Fully Offline Testing (No Internet)

Use `oidc_provider: local` with file-based users. No external network access required at all.

## Per-Service Auth Policy

When Authelia or Authentik is active, each service gets one of three policies:

| Policy | Behavior | Example Services |
|---|---|---|
| `protected` | ext_authz enforced — user must authenticate through SSO | Sonarr, Radarr, Prowlarr, Jellyseerr |
| `native` | Service handles its own auth — ext_authz bypassed | Jellyfin, Plex, Emby (TV/mobile apps can't do OIDC) |
| `public` | No auth at all | Authelia itself, Envoy, webhook endpoints |

Override per-service policy in the profile:

```yaml
auth:
  per_service:
    jellyfin: native       # default — keep built-in auth for device clients
    jellyseerr: protected  # SSO login for web UI
    homepage: protected    # dashboard behind SSO
```

## Forwarded Identity Headers

When a user authenticates through Authelia, these headers are forwarded to upstream services:

| Header | Content | Example |
|---|---|---|
| `Remote-User` | Username | `admin` |
| `Remote-Name` | Display name | `Media Stack Admin` |
| `Remote-Email` | Email address | `admin@local` |
| `Remote-Groups` | Comma-separated groups | `admins` |

The dashboard reads these headers to display the authenticated user in the header bar.

For Authentik, the equivalent headers are `X-authentik-username`, `X-authentik-name`, `X-authentik-email`, `X-authentik-groups`, and `X-authentik-uid`.

## Troubleshooting

### Login redirects in a loop

- Check that `auth.media-stack.local` resolves correctly from the browser machine
- Verify Authelia's `session.cookies[].domain` matches your base domain
- Check Authelia logs: `docker logs authelia`

### "Invalid redirect URI" from Google

- Ensure the redirect URI registered at Google **exactly** matches `http://auth.media-stack.local:8880/api/oidc/callback` (including port and protocol)
- Google is strict about trailing slashes — don't add one

### Dashboard shows no user identity

- Verify auth mode is `authelia` or `authentik` (not `none` or `basic`)
- Check that Envoy's ext_authz filter is forwarding `Remote-User` headers
- Call `GET /api/auth/identity` directly to see what the controller receives

### Services still ask for login after SSO

- When SSO is active, per-app Forms auth is automatically disabled (`app_auth.method: None`)
- If a service still prompts, check its individual auth settings in the service UI

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
