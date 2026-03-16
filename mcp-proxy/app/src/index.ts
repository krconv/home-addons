#!/usr/bin/env node
/**
 * MCP Proxy — aggregates multiple upstream MCP servers into one endpoint.
 *
 * Upstream servers and their headers are configured via the HA add-on UI
 * and passed in as the UPSTREAMS_JSON environment variable.
 *
 * On first start an API key is generated at /data/api_key and logged once.
 * Add it to your MCP client as: Authorization: Bearer <key>
 *
 * All tools are prefixed with the upstream name:
 *   copilot__get_accounts, simplifi__list_transactions, etc.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
  type Tool,
  type CallToolResult,
} from "@modelcontextprotocol/sdk/types.js";
import express, { type Request, type Response, type NextFunction } from "express";
import fs from "fs";
import path from "path";
import crypto from "crypto";

// ── Types ─────────────────────────────────────────────────────────────────────

interface RawHeader {
  name: string;
  value: string;
}

// Shape coming from HA config (headers as {name,value} list)
interface RawUpstreamConfig {
  name: string;
  url: string;
  headers?: RawHeader[];
}

// Normalised internal shape (headers as Record)
interface UpstreamConfig {
  name: string;
  url: string;
  headers: Record<string, string>;
}

interface ConnectedUpstream {
  config: UpstreamConfig;
  client: Client;
  tools: Tool[];
}

interface AuthCode {
  challenge: string;
  redirectUri: string;
  expiresAt: number;
}

// ── Auth code store (authorization code + PKCE flow) ──────────────────────────

const authCodes = new Map<string, AuthCode>();

function pruneAuthCodes(): void {
  const now = Date.now();
  for (const [code, data] of authCodes) {
    if (data.expiresAt < now) authCodes.delete(code);
  }
}

function verifyS256(verifier: string, challenge: string): boolean {
  const hash = crypto.createHash("sha256").update(verifier).digest("base64")
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
  return hash === challenge;
}

function escHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── Constants ─────────────────────────────────────────────────────────────────

const DATA_DIR = process.env.DATA_DIR ?? "/data";
const PORT = parseInt(process.env.PORT ?? "9000", 10);
const API_KEY_FILE = path.join(DATA_DIR, "api_key");

// ── API key management ────────────────────────────────────────────────────────

function loadOrCreateApiKey(): string {
  fs.mkdirSync(DATA_DIR, { recursive: true });

  if (fs.existsSync(API_KEY_FILE)) {
    return fs.readFileSync(API_KEY_FILE, "utf8").trim();
  }

  const key = crypto.randomBytes(32).toString("hex");
  fs.writeFileSync(API_KEY_FILE, key, { mode: 0o600 });
  console.log("Generated new API key, saved to:", API_KEY_FILE);
  return key;
}

// ── Config management ─────────────────────────────────────────────────────────

function parseHeaders(headers?: RawHeader[]): Record<string, string> {
  if (!headers || headers.length === 0) return {};
  return Object.fromEntries(headers.map((h) => [h.name, h.value]));
}

function loadConfig(): UpstreamConfig[] {
  const raw = process.env.UPSTREAMS_JSON;
  if (!raw) {
    console.warn("UPSTREAMS_JSON is not set — no upstreams configured.");
    return [];
  }
  try {
    const parsed = JSON.parse(raw) as RawUpstreamConfig[];
    return parsed.map((u) => ({
      name: u.name,
      url: u.url,
      headers: parseHeaders(u.headers),
    }));
  } catch (err) {
    console.error("Failed to parse UPSTREAMS_JSON:", err);
    return [];
  }
}

// ── Upstream connections ──────────────────────────────────────────────────────

async function connectUpstream(config: UpstreamConfig): Promise<ConnectedUpstream> {
  const client = new Client({ name: "mcp-proxy", version: "1.0.0" });

  const transport = new StreamableHTTPClientTransport(new URL(config.url), {
    requestInit: { headers: config.headers },
  });

  await client.connect(transport);

  const { tools } = await client.listTools();
  console.log(`[${config.name}] Connected — ${tools.length} tool(s) available`);

  return { config, client, tools };
}

async function connectAll(
  configs: UpstreamConfig[]
): Promise<Map<string, ConnectedUpstream>> {
  const upstreams = new Map<string, ConnectedUpstream>();

  for (const config of configs) {
    try {
      const upstream = await connectUpstream(config);
      upstreams.set(config.name, upstream);
    } catch (err) {
      console.error(
        `[${config.name}] Failed to connect to ${config.url}:`,
        err instanceof Error ? err.message : err
      );
      console.error(`[${config.name}] Tools from this upstream will not be available.`);
    }
  }

  return upstreams;
}

// ── MCP proxy server ──────────────────────────────────────────────────────────

function buildMcpServer(upstreams: Map<string, ConnectedUpstream>): Server {
  const server = new Server(
    { name: "mcp-proxy", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  // Aggregate tools from all upstreams, prefixed with upstream name
  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const tools: Tool[] = [];
    for (const [name, upstream] of upstreams) {
      for (const tool of upstream.tools) {
        tools.push({
          ...tool,
          name: `${name}__${tool.name}`,
          description: `[${name}] ${tool.description ?? ""}`.trim(),
        });
      }
    }
    return { tools };
  });

  // Route tool calls to the correct upstream
  server.setRequestHandler(CallToolRequestSchema, async (request): Promise<CallToolResult> => {
    const toolName = request.params.name;
    const sep = toolName.indexOf("__");

    if (sep === -1) {
      return {
        isError: true,
        content: [{
          type: "text",
          text: `Error: Tool "${toolName}" has no upstream prefix. Expected format: <upstream>__<tool>`,
        }],
      };
    }

    const upstreamName = toolName.slice(0, sep);
    const upstreamTool = toolName.slice(sep + 2);
    const upstream = upstreams.get(upstreamName);

    if (!upstream) {
      return {
        isError: true,
        content: [{
          type: "text",
          text: `Error: Unknown upstream "${upstreamName}". Available: ${[...upstreams.keys()].join(", ")}`,
        }],
      };
    }

    try {
      const result = await upstream.client.callTool({
        name: upstreamTool,
        arguments: request.params.arguments,
      });
      return result as CallToolResult;
    } catch (err) {
      return {
        isError: true,
        content: [{
          type: "text",
          text: `Error calling "${upstreamTool}" on "${upstreamName}": ${err instanceof Error ? err.message : String(err)}`,
        }],
      };
    }
  });

  return server;
}

// ── HTTP server ───────────────────────────────────────────────────────────────

// Derive the externally-visible base URL from the incoming request.
// Respects X-Forwarded-Proto so reverse proxies (HA ingress, nginx, etc.) work correctly.
function getBaseUrl(req: Request): string {
  const proto = (req.headers["x-forwarded-proto"] as string | undefined)?.split(",")[0].trim() ?? "http";
  const host = req.headers["host"] ?? `localhost:${PORT}`;
  return `${proto}://${host}`;
}

function serverMetadata(base: string): object {
  return {
    issuer: base,
    authorization_endpoint: `${base}/authorize`,
    token_endpoint: `${base}/token`,
    grant_types_supported: ["authorization_code"],
    response_types_supported: ["code"],
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["none"],
  };
}

function startHttpServer(mcpServer: Server, apiKey: string): void {
  const app = express();
  app.use(express.json());
  app.use(express.urlencoded({ extended: false }));

  // OAuth 2.0 protected resource metadata (RFC 9728) — root variant
  app.get("/.well-known/oauth-protected-resource", (req: Request, res: Response): void => {
    const base = getBaseUrl(req);
    res.json({ resource: base, authorization_servers: [base], bearer_methods_supported: ["header"] });
  });

  // OAuth 2.0 protected resource metadata (RFC 9728) — path-qualified for /mcp
  app.get("/.well-known/oauth-protected-resource/mcp", (req: Request, res: Response): void => {
    const base = getBaseUrl(req);
    res.json({ resource: `${base}/mcp`, authorization_servers: [base], bearer_methods_supported: ["header"] });
  });

  // OAuth 2.0 authorization server metadata (RFC 8414)
  app.get("/.well-known/oauth-authorization-server", (req: Request, res: Response): void => {
    res.json(serverMetadata(getBaseUrl(req)));
  });

  // OpenID Connect discovery — used by claude.ai as a fallback
  app.get("/.well-known/openid-configuration", (req: Request, res: Response): void => {
    res.json(serverMetadata(getBaseUrl(req)));
  });

  // Authorization endpoint — browser-based Authorization Code + PKCE flow (used by claude.ai)
  app.get("/authorize", (req: Request, res: Response): void => {
    const q = req.query as Record<string, string>;
    if (q.response_type !== "code") {
      res.status(400).json({ error: "unsupported_response_type" });
      return;
    }
    if (q.code_challenge_method && q.code_challenge_method !== "S256") {
      res.status(400).json({ error: "invalid_request", error_description: "Only S256 supported" });
      return;
    }
    const fields = ["response_type", "client_id", "redirect_uri", "code_challenge",
      "code_challenge_method", "state", "scope", "resource"];
    const hiddenInputs = fields
      .filter((f) => q[f] != null)
      .map((f) => `<input type="hidden" name="${escHtml(f)}" value="${escHtml(q[f])}">`)
      .join("\n    ");
    res.send(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MCP Proxy — Authorize</title>
  <style>
    body{font-family:system-ui,sans-serif;max-width:380px;margin:80px auto;padding:0 20px}
    h1{font-size:1.2rem;margin-bottom:.5rem}
    p{font-size:.875rem;color:#555;margin-bottom:1.5rem}
    label{display:block;font-size:.8rem;font-weight:600;margin-bottom:4px}
    input[type=password]{width:100%;box-sizing:border-box;padding:8px 10px;font-size:1rem;border:1px solid #ccc;border-radius:6px}
    button{margin-top:14px;width:100%;padding:10px;font-size:1rem;background:#111;color:#fff;border:none;border-radius:6px;cursor:pointer}
    button:hover{background:#333}
    .hint{font-size:.75rem;color:#999;margin-top:20px}
  </style>
</head>
<body>
  <h1>Authorize MCP Proxy</h1>
  <p>Enter your API key to grant access to this MCP server.</p>
  <form method="POST" action="/authorize">
    ${hiddenInputs}
    <label for="api_key">API Key</label>
    <input type="password" id="api_key" name="api_key" autocomplete="current-password" autofocus>
    <button type="submit">Authorize</button>
  </form>
  <p class="hint">Find your API key in the MCP Proxy add-on logs in Home Assistant.</p>
</body>
</html>`);
  });

  app.post("/authorize", (req: Request, res: Response): void => {
    const body = req.body as Record<string, string>;
    const { redirect_uri, code_challenge, state, api_key } = body;

    if (!redirect_uri) {
      res.status(400).json({ error: "invalid_request", error_description: "redirect_uri required" });
      return;
    }

    let redirectUrl: URL;
    try {
      redirectUrl = new URL(redirect_uri);
    } catch {
      res.status(400).json({ error: "invalid_request", error_description: "invalid redirect_uri" });
      return;
    }

    if (api_key !== apiKey) {
      redirectUrl.searchParams.set("error", "access_denied");
      if (state) redirectUrl.searchParams.set("state", state);
      res.redirect(redirectUrl.toString());
      return;
    }

    pruneAuthCodes();
    const code = crypto.randomBytes(32).toString("hex");
    authCodes.set(code, {
      challenge: code_challenge,
      redirectUri: redirect_uri,
      expiresAt: Date.now() + 5 * 60 * 1000,
    });

    redirectUrl.searchParams.set("code", code);
    if (state) redirectUrl.searchParams.set("state", state);
    res.redirect(redirectUrl.toString());
  });

  // Token endpoint — authorization_code + PKCE only
  app.post("/token", (req: Request, res: Response): void => {
    const body = req.body as Record<string, string>;

    if (body.grant_type !== "authorization_code") {
      res.status(400).json({ error: "unsupported_grant_type" });
      return;
    }

    const { code, code_verifier, redirect_uri } = body;
    const stored = authCodes.get(code);

    if (!stored || stored.expiresAt < Date.now()) {
      res.status(400).json({ error: "invalid_grant", error_description: "Code not found or expired" });
      return;
    }
    if (stored.redirectUri !== redirect_uri) {
      res.status(400).json({ error: "invalid_grant", error_description: "redirect_uri mismatch" });
      return;
    }
    if (!verifyS256(code_verifier, stored.challenge)) {
      res.status(400).json({ error: "invalid_grant", error_description: "PKCE verification failed" });
      return;
    }

    authCodes.delete(code);
    res.json({ access_token: apiKey, token_type: "bearer", expires_in: 3600 });
  });

  // Bearer token auth for all other routes
  app.use((req: Request, res: Response, next: NextFunction): void => {
    const auth = req.headers["authorization"];
    if (!auth || auth !== `Bearer ${apiKey}`) {
      const base = getBaseUrl(req);
      res.set(
        "WWW-Authenticate",
        `Bearer realm="${base}", resource_metadata="${base}/.well-known/oauth-protected-resource/mcp"`
      );
      res.status(401).json({ error: "Unauthorized" });
      return;
    }
    next();
  });

  // Stateless MCP endpoint — new transport per request
  app.post("/mcp", async (req: Request, res: Response): Promise<void> => {
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    res.on("close", () => transport.close());
    await mcpServer.connect(transport);
    await transport.handleRequest(req, res, req.body);
  });

  app.listen(PORT, () => {
    console.log(`MCP proxy listening on http://localhost:${PORT}/mcp`);
    console.log(`OAuth authorize endpoint: http://localhost:${PORT}/authorize`);
  });
}

// ── Entry point ───────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const apiKey = loadOrCreateApiKey();
  console.log("=".repeat(64));
  console.log("MCP client auth header:");
  console.log(`  Authorization: Bearer ${apiKey}`);
  console.log("=".repeat(64));
  const upstreamConfigs = loadConfig();
  const upstreams = await connectAll(upstreamConfigs);

  if (upstreams.size === 0) {
    console.warn("No upstreams connected — proxy will serve an empty tool list.");
  }

  const mcpServer = buildMcpServer(upstreams);
  startHttpServer(mcpServer, apiKey);

  console.log(
    `Aggregating tools from: ${[...upstreams.keys()].join(", ") || "(none)"}`
  );
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
