import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

const server = new McpServer({ name: "clean-ts", version: "1.0.0" });

server.tool("search", "Search items", {
  query: z.string().describe("Search term"),
  limit: z.number().max(100).default(10),
}, async ({ query, limit }) => {
  // Safe: no dangerous sinks
  return { results: [] };
});
