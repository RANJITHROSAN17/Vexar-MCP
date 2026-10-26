import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

const server = new McpServer({ name: "clean-ts", version: "1.0.0" });

server.tool(
  "search",
  "Search indexed items by keyword with pagination.",
  {
    query: z.string().describe("Free-text search term"),
    limit: z.number().max(100).min(1).optional().default(10).describe("Page size"),
    filters: z.object({
      category: z.string().optional(),
      dateRange: z.object({ start: z.string(), end: z.string() }).optional(),
    }).optional().describe("Advanced filters"),
  },
  async ({ query, limit = 10, filters }) => {
    // Clean: no dangerous sinks; timeout present in hypothetical HTTP call
    const result = { query, limit, filters };
    return result;
  }
);
