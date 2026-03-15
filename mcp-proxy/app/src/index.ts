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

  console.log("=".repeat(64));
  console.log("Generated new API key — add to your MCP client as:");
  console.log(`  Authorization: Bearer ${key}`);
  console.log("Key saved to:", API_KEY_FILE);
  console.log("=".repeat(64));

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

function startHttpServer(mcpServer: Server, apiKey: string): void {
  const app = express();
  app.use(express.json());

  // Bearer token auth
  app.use((req: Request, res: Response, next: NextFunction): void => {
    const auth = req.headers["authorization"];
    if (!auth || auth !== `Bearer ${apiKey}`) {
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
  });
}

// ── Entry point ───────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const apiKey = loadOrCreateApiKey();
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
