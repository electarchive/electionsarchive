import type { AnyProcedure } from "@orpc/server";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { getOpenAPIMeta } from "@orpc/openapi";
import { call, ORPCError } from "@orpc/server";
import { ValibotToJsonSchemaConverter } from "@orpc/valibot";
import * as v from "valibot";
import { keyOwner } from "#lib/server/rpc/keyed.js";
import { router } from "#lib/server/rpc/router.js";
import { count, introduce } from "#lib/server/usage.js";

const TOOLS: Array<{
  name: string;
  procedure: AnyProcedure;
  description?: string;
}> = [
  {
    name: "search",
    procedure: router.search.query,
    description:
      "Search the archive's polities, elections, contests, people, parties, chambers, and places by name; start here to find IDs",
  },
  { name: "list_polities", procedure: router.polities.list },
  { name: "get_polity", procedure: router.polities.get },
  { name: "get_election", procedure: router.elections.get },
  { name: "get_contest", procedure: router.contests.get },
  { name: "get_election_indices", procedure: router.indices.election },
  { name: "get_polity_indices", procedure: router.indices.polity },
  { name: "get_contest_indices", procedure: router.indices.contest },
  { name: "get_unmapped_units", procedure: router.contests.ledger },
  { name: "get_live_results", procedure: router.tonight.results },
  { name: "get_live_race", procedure: router.tonight.race },
  { name: "get_person", procedure: router.people.get },
  { name: "get_party", procedure: router.parties.get },
  { name: "get_party_elections", procedure: router.parties.elections },
  { name: "get_chamber", procedure: router.bodies.get },
  { name: "get_chamber_members", procedure: router.bodies.members },
  { name: "get_page_path", procedure: router.entities.path },
  { name: "get_coverage_gaps", procedure: router.coverage.gaps },
  { name: "get_history", procedure: router.histories.list },
  { name: "get_change", procedure: router.histories.change },
  { name: "recent_changes", procedure: router.recentChanges.list },
];

const LIVE =
  " On election night, get_live_results has the national picture and every race, and get_live_race has one race in full.";

const INSTRUCTIONS =
  "electionsarchive.com holds election results, candidates, parties, chambers, and boundaries, with every number traced to a source. Find IDs with search, then read records with the get_ tools." +
  LIVE +
  " Every tool only reads; none changes the archive.";

const WAIT_MS = 25_000;

const LARGEST = 400_000;

const NARROW: Record<string, string> = {
  get_election:
    "Read one state's own election instead (search finds it, such as 2024 Pennsylvania general election), pass within for a UK nation or Canadian province, or read one contest with get_contest.",
  get_contest:
    "Leave out by for the totals, or pass unit to look inside one place.",
  get_chamber: "Read one term's members with get_chamber_members instead.",
  get_polity: "Leave out all, or list the places below it with list_polities.",
};

const SLOW = Symbol("slow");

const converter = new ValibotToJsonSchemaConverter();

const Shape = v.looseObject({
  properties: v.optional(
    v.record(v.string(), v.looseObject({ type: v.optional(v.unknown()) })),
    {},
  ),
});

const catalog = TOOLS.map((tool) => {
  const input = tool.procedure["~orpc"].inputSchemas?.at(-1);
  const converted: unknown =
    input == null ? {} : converter.convert(input, "input")[0];
  const schema = v.parse(Shape, converted);
  const strings = new Set(
    Object.entries(schema.properties)
      .filter(([, property]) => property.type === "string")
      .map(([name]) => name),
  );
  return {
    ...tool,
    strings,
    listing: {
      name: tool.name,
      description:
        tool.description ?? getOpenAPIMeta(tool.procedure)?.summary ?? "",
      inputSchema: { ...schema, type: "object" as const },
    },
  };
});

const byName = new Map(catalog.map((tool) => [tool.name, tool]));

const Key = v.union([v.string(), v.number()]);

const Issues = v.object({
  issues: v.array(
    v.looseObject({
      message: v.string(),
      path: v.optional(
        v.array(v.union([Key, v.looseObject({ key: Key })])),
        [],
      ),
    }),
  ),
});

function failure(text: string) {
  return { isError: true, content: [{ type: "text" as const, text }] };
}

function explain(message: string, data: unknown) {
  const parsed = v.safeParse(Issues, data);
  if (!parsed.success) {
    return message;
  }
  const lines = [message];
  for (const issue of parsed.output.issues) {
    const at = issue.path
      .map((step) => String(typeof step === "object" ? step.key : step))
      .join(".");
    lines.push(at === "" ? issue.message : `${at}: ${issue.message}`);
  }
  return lines.join("\n");
}

function build(
  headers: Headers,
  owner: { keyId: string; userId: string } | null,
) {
  const server = new Server(
    { name: "electionsarchive", title: "electionsarchive.com", version: "1" },
    { capabilities: { tools: {} }, instructions: INSTRUCTIONS },
  );
  server.setRequestHandler(ListToolsRequestSchema, () => ({
    tools: catalog.map((tool) => tool.listing),
  }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const tool = byName.get(request.params.name);
    if (tool == null) {
      return failure(`There is no tool named ${request.params.name}.`);
    }
    count(headers, owner, tool.name);
    const input: Record<string, unknown> = {};
    const given = Object.entries(request.params.arguments ?? {});
    for (const [name, value] of given) {
      input[name] =
        typeof value === "number" && tool.strings.has(name)
          ? String(value)
          : value;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const running: Promise<unknown> = call(tool.procedure, input, {
        context: { headers },
      });
      running.catch(() => null);
      const output = await Promise.race([
        running,
        new Promise<typeof SLOW>((resolve) => {
          timer = setTimeout(() => {
            resolve(SLOW);
          }, WAIT_MS);
        }),
      ]);
      if (output === SLOW) {
        return failure(
          "The archive is still working on that answer and will keep it ready. Call the tool again in a minute.",
        );
      }
      const text = JSON.stringify(output);
      if (text.length > LARGEST) {
        return failure(
          `That answer is ${String(Math.round(text.length / 1000))} KB, too large to send. ${NARROW[tool.name] ?? "Narrow it with the tool's filters or a smaller limit."}`,
        );
      }
      return { content: [{ type: "text" as const, text }] };
    } catch (error_) {
      if (error_ instanceof ORPCError) {
        return failure(explain(error_.message, error_.data));
      }
      console.error(request.params.name, error_);
      return failure(
        "The archive couldn't answer that just now. Try again in a minute.",
      );
    } finally {
      clearTimeout(timer);
    }
  });
  return server;
}

export async function handle(request: Request) {
  const { headers } = request;
  let owner: Awaited<ReturnType<typeof keyOwner>> = null;
  try {
    owner = await keyOwner(headers);
  } catch (error_) {
    if (error_ instanceof ORPCError) {
      return Response.json(
        {
          error:
            error_.code === "FORBIDDEN"
              ? "This key's owner is banned."
              : "This API key isn't valid. Create one at /settings/keys.",
        },
        {
          status: error_.code === "FORBIDDEN" ? 403 : 401,
          headers: { "www-authenticate": 'Bearer realm="electionsarchive"' },
        },
      );
    }
    throw error_;
  }
  const server = build(headers, owner);
  const transport = new WebStandardStreamableHTTPServerTransport({
    enableJsonResponse: true,
  });
  await server.connect(transport);
  try {
    const response = await transport.handleRequest(request);
    const client = server.getClientVersion()?.name;
    if (client != null) {
      introduce(headers, owner, client);
    }
    return response;
  } finally {
    await server.close();
  }
}

export const tools = catalog.map((tool) => ({
  name: tool.name,
  description: tool.listing.description,
}));
