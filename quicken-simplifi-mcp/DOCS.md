# Quicken Simplifi MCP

This add-on runs a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that integrates with [Quicken Simplifi](https://www.quicken.com/simplifi/), providing AI clients (like Claude) access to your transaction data.

## Configuration

### Required

- `public_base_url`: The publicly accessible URL of this server (e.g., `https://simplifi-mcp.example.com`). Used for OAuth issuer and redirect flows.
- `simplifi_email`: Your Quicken Simplifi account email.
- `simplifi_password`: Your Quicken Simplifi account password.
- `simplifi_dataset_id`: Your Simplifi dataset identifier.
- `simplifi_threat_metrix_session_id`: Required for the Simplifi authentication flow.
- `oauth_login_username`: Username for MCP clients to authenticate (default: `admin`).
- `oauth_login_password`: Password for MCP clients to authenticate.
- `oauth_jwt_secret`: Secret for signing JWT tokens (use a strong random string, 32+ characters).

### Optional

- `oauth_allowed_redirect_uris`: Comma-separated list of allowed OAuth redirect URIs for MCP clients.

## Ports

- `8787/tcp`: HTTP server (MCP endpoint, OAuth, health check). Disabled by default; enable in the add-on network config.

## MCP Endpoints

- `POST /mcp` - MCP transport (SSE/streamable HTTP)
- `GET /oauth/authorize` - OAuth authorization
- `POST /oauth/token` - OAuth token exchange
- `GET /healthz` - Health check

## Available Tools

Once connected, MCP clients can use these tools:

- **list_transactions** - Retrieve paginated transaction lists with filtering
- **search_transactions** - Full-text search across transactions
- **get_transaction** - Fetch individual transaction details
- **update_transaction** - Modify transaction data
