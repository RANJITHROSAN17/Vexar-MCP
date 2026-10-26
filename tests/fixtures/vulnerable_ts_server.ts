import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { exec } from "child_process";

const server = new McpServer({ name: "vuln-ts", version: "1.0.0" });

server.tool("run", "Run command", {
  cmd: z.string(),
}, async ({ cmd }) => {
  const result = exec(cmd); // dangerous shell sink
  return { output: "done" };
});

server.tool("eval_script", "Eval", {
  code: z.string(),
}, async ({ code }) => {
  const out = eval(code); // dangerous eval
  return { out };
});
