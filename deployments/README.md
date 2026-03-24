# Plane MCP Server — Production Deployment

This folder contains production deployment configurations for the Plane MCP Server.

> **Note**: These setups use the published Docker image. For local development, see the [Local Development](../README.md#local-development) section in the root README.

---

## Option 1: Docker Compose

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2+

### Setup

```bash
cd deployments

# 1. Edit variables.env with your values
#    (fill in OAuth credentials and Plane API URL)
vi variables.env

# 2. Start the server
docker compose up -d

# 3. Check logs
docker compose logs -f mcp
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| `mcp` | `8211` | Plane MCP Server (HTTP mode) |
| `valkey` | — | Token storage for OAuth (internal only) |

### Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `http://<host>:8211/mcp` | OAuth | OAuth-based MCP endpoint |
| `http://<host>:8211/http/api-key/mcp` | PAT header | Personal Access Token endpoint |
| `http://<host>:8211/sse` | OAuth | Legacy SSE endpoint (deprecated) |

### Configuration

All configuration is done via `variables.env`. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `APP_RELEASE_VERSION` | No | Image tag to deploy (default: `latest`) |
| `PLANE_BASE_URL` | No | Plane API URL (default: `https://api.plane.so`) |
| `PLANE_INTERNAL_BASE_URL` | No | Internal API URL for server-to-server calls |
| `PLANE_OAUTH_PROVIDER_CLIENT_ID` | Yes | OAuth client ID |
| `PLANE_OAUTH_PROVIDER_CLIENT_SECRET` | Yes | OAuth client secret |
| `PLANE_OAUTH_PROVIDER_BASE_URL` | Yes | Public URL the server is reachable on |

### Upgrading

```bash
# Pull the latest image and restart
docker compose pull
docker compose up -d
```

---

## Option 2: Helm Chart

### Prerequisites

- Kubernetes cluster (v1.21+)
- [Helm](https://helm.sh/docs/intro/install/) v3+
- An ingress controller (e.g. nginx)

### Add the Helm Repository

```bash
helm repo add plane https://helm.plane.so
helm repo update
```

### Install

```bash
helm install plane-mcp plane/plane-mcp-server \
  --namespace plane-mcp \
  --create-namespace \
  -f values.yaml
```

### Minimal `values.yaml`

```yaml
ingress:
  enabled: true
  host: mcp.yourdomain.com
  ingressClass: nginx
  ssl:
    enabled: true
    issuer: cloudflare   # cloudflare | digitalocean | http
    email: you@yourdomain.com

services:
  api:
    plane_base_url: 'https://api.plane.so'
    plane_oauth:
      enabled: true
      client_id: '<your-oauth-client-id>'
      client_secret: '<your-oauth-client-secret>'
      provider_base_url: 'https://mcp.yourdomain.com'
```

### Key Values

| Value | Default | Description |
|-------|---------|-------------|
| `dockerRegistry.default_tag` | `latest` | Image tag to deploy |
| `ingress.enabled` | `true` | Enable ingress |
| `ingress.host` | `mcp.example.com` | Public hostname |
| `ingress.ingressClass` | `nginx` | Ingress class name |
| `ingress.ssl.enabled` | `false` | Enable TLS via cert-manager |
| `ingress.ssl.issuer` | `cloudflare` | ACME issuer (`cloudflare`, `digitalocean`, `http`) |
| `services.api.replicas` | `1` | Number of MCP server replicas |
| `services.api.plane_base_url` | `''` | Plane API URL |
| `services.api.plane_oauth.enabled` | `false` | Enable OAuth endpoints |
| `services.api.plane_oauth.client_id` | `''` | OAuth client ID |
| `services.api.plane_oauth.client_secret` | `''` | OAuth client secret |
| `services.api.plane_oauth.provider_base_url` | `''` | Public URL the server is reachable on |
| `services.redis.local_setup` | `true` | Deploy Valkey in-cluster |
| `services.redis.external_redis_url` | `''` | External Valkey/Redis URL (if not using in-cluster) |
| `services.proxy.enabled` | `false` | Enable nginx proxy sidecar |

### Upgrade

```bash
helm upgrade plane-mcp plane/plane-mcp-server \
  --namespace plane-mcp \
  -f values.yaml
```

### Uninstall

```bash
helm uninstall plane-mcp --namespace plane-mcp
```

---

## Troubleshooting

**Server not starting?**
```bash
docker compose logs mcp
```

**Valkey connection issues?**
```bash
docker compose exec valkey valkey-cli ping
```

**Reset and start fresh:**
```bash
docker compose down -v   # removes Redis volume too
docker compose up -d
```
