#!/usr/bin/env node
/**
 * MCP Proxy — aggregates multiple upstream MCP servers into one endpoint.
 *
 * On first start, generates an API key written to /data/api_key and creates
 * a default upstream config at /data/upstream_config.json. Edit that file to
 * add custom headers (e.g. Authorization) per upstream server.
 *
 * All tools are exposed with the upstream name as a prefix:
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

interface UpstreamConfig {
  name: string;
  url: string;
  headers?: Record<string, string>;
}

interface ProxyConfig {
  upstreams: UpstreamConfig[];
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
const CONFIG_FILE = path.join(DATA_DIR, "upstream_config.json");

const DEFAULT_CONFIG: ProxyConfig = {
  upstreams: [
    { name: "copilot", url: "http://localhost:3000/mcp", headers: {} },
    { name: "simplifi", url: "http://localhost:8787/mcp", headers: {} },
  ],
};

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

function loadConfig(): ProxyConfig {
  fs.mkdirSync(DATA_DIR, { recursive: true });

  if (!fs.existsSync(CONFIG_FILE)) {
    fs.writeFileSync(CONFIG_FILE, JSON.stringify(DEFAULT_CONFIG, null, 2));
    console.log(`Created default upstream config at ${CONFIG_FILE}`);
    console.log("Edit that file to configure upstream URLs and custom headers.");
    return DEFAULT_CONFIG;
  }

  try {
    const raw = fs.readFileSync(CONFIG_FILE, "utf8");
    return JSON.parse(raw) as ProxyConfig;
  } catch (err) {
    console.error(`Failed to parse ${CONFIG_FILE}:`, err);
    console.error("Falling back to default config.");
    return DEFAULT_CONFIG;
  }
}

// ── Upstream connections ──────────────────────────────────────────────────────

async function connectUpstream(config: UpstreamConfig): Promise<ConnectedUpstream> {
  const client = new Client({ name: "mcp-proxy", version: "1.0.0" });

  const transport = new StreamableHTTPClientTransport(new URL(config.url), {
    requestInit: { headers: config.headers ?? {} },
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
  const config = loadConfig();
  const upstreams = await connectAll(config.upstreams);

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
