# Deploy ChannelWatch behind a reverse proxy

Use this guide to publish ChannelWatch through Nginx, Caddy, Traefik, or Cloudflare Tunnel while keeping the application on its normal internal port, `8501`.


This guide uses `your-domain.example.com` as the public host and `127.0.0.1:8501` as the local ChannelWatch target. Replace only those placeholders.

## When you need a reverse proxy

Use a reverse proxy when you want to:

1. Serve ChannelWatch on HTTPS without changing the container.
2. Put it behind an existing domain, such as `https://your-domain.example.com`.
3. Share ports `80` and `443` with other homelab apps.
4. Add an outer access layer, such as basic auth, Cloudflare Access, Authelia, or another SSO gateway.

You do not need a reverse proxy for a simple LAN only setup where `http://server-ip:8501` is acceptable.

## ChannelWatch expectations

ChannelWatch is served by FastAPI on port `8501`. The static UI and `/api/*` routes are same origin, so the browser should see one public origin, for example `https://your-domain.example.com`.

Set these headers at the proxy:

```text
X-Forwarded-For: <client IP chain>
X-Forwarded-Proto: https
X-Forwarded-Host: your-domain.example.com
Host: your-domain.example.com
```

Important security behavior:

1. `X-Forwarded-Proto` matters for RBAC sessions. ChannelWatch marks session cookies as `Secure` when the request is HTTPS or when `X-Forwarded-Proto` is `https`.
2. CORS preflights are closed by design. Do not split the UI and API across different origins.
3. RBAC session writes use `X-CSRF-Token`. Keep ChannelWatch on one public origin so the UI can read the token and send it back on state changing API requests.
4. This build does not expose `TRUSTED_HOSTS`, `CORS_ORIGINS`, or a trusted host middleware setting. Do not add fake env vars to compose files.
5. If `CW_DISABLE_AUTH=true` is used, state changing API requests with an `Origin` header must match the request `Host`. Prefer leaving auth enabled instead of using this setting as a proxy workaround.

Optional public URL settings:

```yaml
environment:
  CHANNELWATCH_INSTANCE_URL: "https://your-domain.example.com"
  CW_DISABLE_AUTH: "false"
```

`CHANNELWATCH_INSTANCE_URL` is used in outbound webhook payloads. `CW_INSTANCE_URL` and `APP_URL` are compatibility alternatives, but use one public URL setting rather than all three.

## Nginx

Use this server block when Nginx terminates TLS and ChannelWatch listens on the same host at `127.0.0.1:8501`.

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}

server {
    listen 80;
    server_name your-domain.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.example.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

TLS terminates at Nginx. Keep the upstream target as plain HTTP unless you have added TLS inside the ChannelWatch container yourself.

Basic auth add-on hint:

```nginx
auth_basic "ChannelWatch";
auth_basic_user_file /etc/nginx/.htpasswd;
```

If you use an SSO gateway in front of Nginx, let that gateway protect the whole origin. Do not iframe ChannelWatch into another portal because the app sets frame protection headers.

## Caddy

Caddy manages TLS automatically for public DNS names that can pass ACME validation.

```caddyfile
your-domain.example.com {
    reverse_proxy 127.0.0.1:8501 {
        header_up Host {host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host {host}
    }
}
```

Caddy handles HTTP to HTTPS redirects and websocket upgrades for `reverse_proxy` automatically. ChannelWatch does not currently require browser websocket routes, but keeping upgrade support enabled is safe for future proxy reuse.

Basic auth add-on hint:

```caddyfile
your-domain.example.com {
    basicauth {
        channelwatch-user $2a$14$replace_this_with_a_caddy_hash
    }

    reverse_proxy 127.0.0.1:8501 {
        header_up Host {host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host {host}
    }
}
```

Create the password hash with `caddy hash-password`. For SSO, put your identity proxy in front of Caddy or use a Caddy auth plugin you already operate. ChannelWatch does not ship OAuth or SSO integration.

## Traefik

This Docker Compose example lets Traefik terminate TLS and route to a ChannelWatch container on the same Docker network.

```yaml
services:
  traefik:
    image: traefik:v3.2
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.letsencrypt.acme.email=you@example.com
      - --certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.letsencrypt.acme.httpchallenge=true
      - --certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik_letsencrypt:/letsencrypt
    restart: unless-stopped

  channelwatch:
    image: coderluii/channelwatch:latest
    volumes:
      - channelwatch_config:/config
    environment:
      CHANNELWATCH_INSTANCE_URL: "https://your-domain.example.com"
      CW_DISABLE_AUTH: "false"
    labels:
      - traefik.enable=true
      - traefik.http.routers.channelwatch.rule=Host(`your-domain.example.com`)
      - traefik.http.routers.channelwatch.entrypoints=websecure
      - traefik.http.routers.channelwatch.tls.certresolver=letsencrypt
      - traefik.http.services.channelwatch.loadbalancer.server.port=8501
      - traefik.http.middlewares.channelwatch-headers.headers.customrequestheaders.X-Forwarded-Proto=https
      - traefik.http.middlewares.channelwatch-headers.headers.customrequestheaders.X-Forwarded-Host=your-domain.example.com
      - traefik.http.routers.channelwatch.middlewares=channelwatch-headers
    restart: unless-stopped

volumes:
  traefik_letsencrypt:
  channelwatch_config:
```

Traefik sets `X-Forwarded-For` and `Host` for proxied requests. The middleware pins `X-Forwarded-Proto` and `X-Forwarded-Host` to the public HTTPS origin. Websocket upgrades are supported by Traefik automatically.

Basic auth add-on hint:

```yaml
labels:
  - traefik.http.middlewares.channelwatch-auth.basicauth.users=channelwatch-user:replace_with_htpasswd_hash
  - traefik.http.routers.channelwatch.middlewares=channelwatch-auth,channelwatch-headers
```

For SSO, attach the forward auth middleware from the identity proxy you already run, such as Authelia or Authentik. Keep ChannelWatch auth enabled unless you have a specific, tested reason to rely only on the outer proxy.

## Cloudflare Tunnel

Cloudflare Tunnel is useful when you do not want to open inbound firewall ports. The tunnel terminates public TLS at Cloudflare and forwards traffic from `cloudflared` to ChannelWatch.

Create `config.yml` for `cloudflared`:

```yaml
tunnel: channelwatch
credentials-file: /etc/cloudflared/channelwatch.json

ingress:
  - hostname: your-domain.example.com
    service: http://127.0.0.1:8501
    originRequest:
      httpHostHeader: your-domain.example.com
      noTLSVerify: false
  - service: http_status:404
```

Run `cloudflared` on the same host or network namespace that can reach `127.0.0.1:8501`. If it runs in a separate container, use the ChannelWatch service name instead:

```yaml
service: http://channelwatch:8501
```

Cloudflare sets forwarded headers for tunnel traffic. The `httpHostHeader` line keeps the upstream `Host` aligned with the public hostname. Websocket proxying is supported by Cloudflare Tunnel, and no extra ChannelWatch setting is needed for the current UI.

TLS terminates at Cloudflare by default. If you also use TLS between `cloudflared` and an upstream reverse proxy, change the `service` URL to `https://...` and configure certificate verification for that upstream.

Basic auth and SSO add-on hint: use Cloudflare Access policies for the hostname. This protects the public route before requests reach ChannelWatch, but it is separate from ChannelWatch's own auth system.

## Helm ingress

For Kubernetes installs that use the bundled Helm chart, prefer the chart's built-in Ingress template instead of hand-writing a separate resource. Ingress is disabled by default because many homelab clusters expose services differently.

Minimal values example:

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: your-domain.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: channelwatch-tls
      hosts:
        - your-domain.example.com
```

The chart renders a `networking.k8s.io/v1` Ingress and supports custom annotations, multiple hosts/paths, and TLS. ChannelWatch remains a single-replica app because `/config` is writable application state.

## Test it works

1. Open `https://your-domain.example.com` in a browser and confirm the ChannelWatch UI loads.
2. Check the unauthenticated health endpoint through the proxy:

   ```bash
   curl -i https://your-domain.example.com/healthz/live
   ```

   A healthy response is `HTTP/2 200` or `HTTP/1.1 200` with `{"status":"ok"}`.

3. Check the public headers seen by ChannelWatch by logging in and using the UI normally. RBAC login should set a `channelwatch_session` cookie with `Secure` when the public URL is HTTPS.
4. Save a harmless setting, such as toggling a notification option and toggling it back. This confirms same origin API calls and CSRF token handling work through the proxy.
5. If you use outbound webhooks, set `CHANNELWATCH_INSTANCE_URL` to `https://your-domain.example.com` and trigger a test alert.

## Troubleshoot common issues

| Symptom | What to check |
| --- | --- |
| Browser gets `401` on API calls | Confirm ChannelWatch auth is configured and the browser is sending either the RBAC session cookie or `X-API-Key`. Also check that an outer basic auth prompt is not blocking `/api/*` after the UI loads. |
| Browser gets `403` when saving settings | Keep the UI and API on the same origin. Do not strip `X-CSRF-Token`, cookies, `Host`, or forwarded headers. Do not disable CSRF checks. |
| Login works on HTTP but fails on HTTPS | Send `X-Forwarded-Proto: https` from the proxy so secure cookies are set correctly. |
| Mixed content warning | Access ChannelWatch through `https://your-domain.example.com`, not an old `http://` bookmark. If webhook payload links matter, set `CHANNELWATCH_INSTANCE_URL` to the HTTPS URL. |
| Websocket upgrade fails | The current UI does not depend on browser websockets, but generic reverse proxy health checks may still test upgrades. Keep the Nginx `Upgrade` and `Connection` headers, or use Caddy, Traefik, or Cloudflare Tunnel defaults. |
| Redirect loop | Check that only the public proxy redirects HTTP to HTTPS. The upstream target should usually stay `http://127.0.0.1:8501`. |
| Cloudflare Access login loops | Protect the whole hostname and allow the same cookie path for `/`, `/api/*`, and `/healthz/*`. Do not split ChannelWatch across multiple hostnames. |

If the proxy still fails, test ChannelWatch directly from the proxy host first:

```bash
curl -i http://127.0.0.1:8501/healthz/live
```

If that direct request fails, fix the container, port mapping, or Docker network before changing proxy settings.
