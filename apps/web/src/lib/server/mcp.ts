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
import { roles } from "#lib/server/contrib.js";
import { keyOwner } from "#lib/server/rpc/keyed.js";
import { router } from "#lib/server/rpc/router.js";

type Access = "read" | "key" | "admin";

const TOOLS: Array<{
  name: string;
  procedure: AnyProcedure;
  access: Access;
  description?: string;
}> = [
  {
    name: "search",
    procedure: router.search.query,
    access: "read",
    description:
      "Search the archive's polities, elections, contests, people, parties, chambers, and places by name; start here to find IDs",
  },
  { name: "list_polities", procedure: router.polities.list, access: "read" },
  { name: "get_polity", procedure: router.polities.get, access: "read" },
  { name: "get_election", procedure: router.elections.get, access: "read" },
  { name: "get_contest", procedure: router.contests.get, access: "read" },
  {
    name: "get_contest_ledger",
    procedure: router.contests.ledger,
    access: "read",
  },
  { name: "get_person", procedure: router.people.get, access: "read" },
  { name: "get_party", procedure: router.parties.get, access: "read" },
  {
    name: "get_party_elections",
    procedure: router.parties.elections,
    access: "read",
  },
  { name: "get_chamber", procedure: router.bodies.get, access: "read" },
  {
    name: "get_chamber_members",
    procedure: router.bodies.members,
    access: "read",
  },
  { name: "get_page_path", procedure: router.entities.path, access: "read" },
  {
    name: "get_coverage_gaps",
    procedure: router.coverage.gaps,
    access: "read",
  },
  { name: "get_editable", procedure: router.agents.editable, access: "read" },
  { name: "describe_ops", procedure: router.agents.ops, access: "read" },
  {
    name: "find_open_proposals",
    procedure: router.proposals.list,
    access: "read",
  },
  { name: "get_proposal", procedure: router.proposals.get, access: "read" },
  { name: "whoami", procedure: router.agents.whoami, access: "key" },
  {
    name: "draft_proposal",
    procedure: router.proposals.create,
    access: "key",
    description:
      "Draft a proposal from ops and evidence (call describe_ops first) and check it: waits up to 60 s for the compile and checks, then returns the proposal with its tier, findings, and diff, or the compile errors with op paths",
  },
  { name: "check_proposal", procedure: router.proposals.check, access: "key" },
  {
    name: "submit_proposal",
    procedure: router.proposals.submit,
    access: "key",
  },
  {
    name: "revise_proposal",
    procedure: router.proposals.revise,
    access: "key",
  },
  {
    name: "withdraw_proposal",
    procedure: router.proposals.withdraw,
    access: "key",
  },
  { name: "upload_document", procedure: router.uploads.create, access: "key" },
  { name: "get_upload", procedure: router.uploads.get, access: "key" },
  {
    name: "get_upload_page",
    procedure: router.uploads.page,
    access: "key",
    description:
      "One rendered page of a ready upload as an image, to read before marking an evidence region",
  },
  { name: "review_queue", procedure: router.admin.queue, access: "admin" },
  { name: "get_review", procedure: router.admin.proposal, access: "admin" },
  { name: "decide_proposal", procedure: router.admin.decide, access: "admin" },
  {
    name: "write_prereview",
    procedure: router.admin.prereview,
    access: "admin",
  },
  { name: "revert_proposal", procedure: router.admin.revert, access: "admin" },
];

const INSTRUCTIONS =
  "electionsarchive.com holds election results, candidates, parties, chambers, and boundaries, with every number traced to a source. Look before you write: find IDs with search and the get_ tools, read a record's editable rows with get_editable, and check find_open_proposals so you don't duplicate work. Every change is a proposal: describe_ops explains the payload, ops, and evidence; draft_proposal compiles and checks it; submit_proposal sends it to review. Cite evidence for every fact, quoting the source exactly. Write tools need an API key from https://electionsarchive.com/settings/keys, sent as x-api-key or a Bearer token.";

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

const Image = v.object({ data: v.string() });

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

function build(headers: Headers, isStaff: boolean) {
  const server = new Server(
    { name: "electionsarchive", title: "electionsarchive.com", version: "1" },
    { capabilities: { tools: {} }, instructions: INSTRUCTIONS },
  );
  server.setRequestHandler(ListToolsRequestSchema, () => ({
    tools: catalog
      .filter((tool) => tool.access !== "admin" || isStaff)
      .map((tool) => tool.listing),
  }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const tool = byName.get(request.params.name);
    if (tool == null || (!isStaff && tool.access === "admin")) {
      return failure(`There is no tool named ${request.params.name}.`);
    }
    const input: Record<string, unknown> = {};
    const given = Object.entries(request.params.arguments ?? {});
    for (const [name, value] of given) {
      input[name] =
        typeof value === "number" && tool.strings.has(name)
          ? String(value)
          : value;
    }
    try {
      const output: unknown = await call(tool.procedure, input, {
        context: { headers },
      });
      if (tool.name === "get_upload_page" && v.is(Image, output)) {
        return {
          content: [
            {
              type: "image" as const,
              data: output.data,
              mimeType: "image/png",
            },
          ],
        };
      }
      return {
        content: [{ type: "text" as const, text: JSON.stringify(output) }],
      };
    } catch (error_) {
      if (error_ instanceof ORPCError) {
        return failure(explain(error_.message, error_.data));
      }
      throw error_;
    }
  });
  return server;
}

export async function handle(request: Request) {
  const { headers } = request;
  let isStaff = false;
  try {
    const owner = await keyOwner(headers);
    if (owner != null) {
      const allowed = await roles(owner.userId);
      isStaff = allowed.admin || allowed.reviewer;
    }
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
  const server = build(headers, isStaff);
  const transport = new WebStandardStreamableHTTPServerTransport({
    enableJsonResponse: true,
    maxRequestBodySize: 30 * 1024 ** 2,
  });
  await server.connect(transport);
  try {
    return await transport.handleRequest(request);
  } finally {
    await server.close();
  }
}

export const tools = catalog
  .filter((tool) => tool.access !== "admin")
  .map((tool) => ({
    name: tool.name,
    description: tool.listing.description,
    isKeyed: tool.access === "key",
  }));
