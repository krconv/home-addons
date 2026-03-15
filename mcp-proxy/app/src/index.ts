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

function startHttpServer(mcpServer: Server, apiKey: string): void {
  const app = express();
  app.use(express.json());
  app.use(express.urlencoded({ extended: false }));

  // OAuth 2.0 protected resource metadata (RFC 9728) — root variant
  app.get("/.well-known/oauth-protected-resource", (req: Request, res: Response): void => {
    const base = getBaseUrl(req);
    res.json({
      resource: base,
      authorization_servers: [base],
      bearer_methods_supported: ["header"],
    });
  });

  // OAuth 2.0 protected resource metadata (RFC 9728) — path-qualified for /mcp
  // Claude Code derives this URL by appending the resource path to /.well-known/oauth-protected-resource
  app.get("/.well-known/oauth-protected-resource/mcp", (req: Request, res: Response): void => {
    const base = getBaseUrl(req);
    res.json({
      resource: `${base}/mcp`,
      authorization_servers: [base],
      bearer_methods_supported: ["header"],
    });
  });

  // OAuth 2.0 authorization server metadata (RFC 8414)
  app.get("/.well-known/oauth-authorization-server", (req: Request, res: Response): void => {
    const base = getBaseUrl(req);
    res.json({
      issuer: base,
      token_endpoint: `${base}/token`,
      grant_types_supported: ["client_credentials"],
      token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
    });
  });

  // OpenID Connect discovery (fallback used by some clients including Claude Code)
  app.get("/.well-known/openid-configuration", (req: Request, res: Response): void => {
    const base = getBaseUrl(req);
    res.json({
      issuer: base,
      token_endpoint: `${base}/token`,
      grant_types_supported: ["client_credentials"],
      token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
    });
  });

  // OAuth 2.0 token endpoint — client_credentials grant
  // Accepts credentials in POST body (client_secret_post) or Basic auth (client_secret_basic)
  app.post("/token", (req: Request, res: Response): void => {
    const body = req.body as Record<string, string>;

    let clientId: string | undefined = body.client_id;
    let clientSecret: string | undefined = body.client_secret;
    const basicHeader = req.headers["authorization"];
    if (basicHeader?.startsWith("Basic ")) {
      const decoded = Buffer.from(basicHeader.slice(6), "base64").toString("utf8");
      const colon = decoded.indexOf(":");
      if (colon !== -1) {
        clientId = decoded.slice(0, colon);
        clientSecret = decoded.slice(colon + 1);
      }
    }

    if (body.grant_type !== "client_credentials") {
      res.status(400).json({ error: "unsupported_grant_type" });
      return;
    }
    if (clientId !== "client" || clientSecret !== apiKey) {
      res.status(401).json({ error: "invalid_client" });
      return;
    }

    res.json({
      access_token: apiKey,
      token_type: "bearer",
      expires_in: 3600,
    });
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
    console.log(`OAuth token endpoint: http://localhost:${PORT}/token  (client_id=client, client_secret=<api_key>)`);
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
