import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { exec, execSync } from "child_process";

const server = new McpServer({ name: "vuln-ts", version: "1.0.0" });

server.tool("run", "Run shell command", {
  cmd: z.string(),
}, async ({ cmd }) => {
  const out = exec(cmd); // shell sink (import-tracked)
  return { output: out?.toString() };
});

server.tool("eval_script", "Evaluate code", {
  code: z.string(),
}, async ({ code }) => {
  const result = eval(code); // eval sink
  return { result };
});

server.tool("fetch_no_timeout", "Fetch remote data (missing timeout)", {
  url: z.string(),
}, async ({ url }) => {
  // HTTP sink without timeout -> MCP010 flag
  const resp = fetch(url); // no timeout= keyword
  return { status: resp?.status ?? 0 };
});
