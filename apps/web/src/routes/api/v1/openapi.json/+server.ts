import { READ_ONLY } from "$app/env/public";
import { OpenAPIGenerator } from "@orpc/openapi";
import { ValibotToJsonSchemaConverter } from "@orpc/valibot";
import { json } from "@sveltejs/kit";
import { reads, router } from "#lib/server/rpc/router.js";
import type { RequestHandler } from "./$types";

const spec = new OpenAPIGenerator({
  converters: [new ValibotToJsonSchemaConverter()],
}).generate(READ_ONLY ? reads : router, {
  version: "3.1.0",
  base: {
    info: { title: "electionsarchive.com", version: "1" },
    servers: [{ url: "/api/v1" }],
  },
});

export const GET: RequestHandler = async () => json(await spec);
