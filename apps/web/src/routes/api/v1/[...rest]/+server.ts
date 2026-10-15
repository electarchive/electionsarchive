import { OpenAPIHandler } from "@orpc/openapi/fetch";
import { router } from "#lib/server/rpc/router.js";
import type { RequestHandler } from "./$types";

const handler = new OpenAPIHandler(router);

const handle: RequestHandler = async ({ request }) => {
  const { response } = await handler.handle(request, { prefix: "/api/v1" });
  return response ?? new Response("Not found", { status: 404 });
};

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
