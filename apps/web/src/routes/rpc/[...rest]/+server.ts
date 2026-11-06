import { RPCHandler } from "@orpc/server/fetch";
import { isRead } from "#lib/rpc-reads.js";
import { served } from "#lib/server/rpc/router.js";
import type { RequestHandler } from "./$types";

const handler = new RPCHandler(served, {
  allowMethods: (method, _procedure, path) =>
    method === "GET"
      ? isRead(path[0])
      : ["POST", "PUT", "PATCH", "DELETE"].includes(method),
});

const handle: RequestHandler = async ({ request }) => {
  const { response } = await handler.handle(request, {
    prefix: "/rpc",
    context: { headers: request.headers },
  });
  return response ?? new Response("Not found", { status: 404 });
};

export const GET = handle;
export const POST = handle;
