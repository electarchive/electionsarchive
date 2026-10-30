import { RPCHandler } from "@orpc/server/fetch";
import { router } from "#lib/server/rpc/router.js";
import type { RequestHandler } from "./$types";

const handler = new RPCHandler(router);

const handle: RequestHandler = async ({ request }) => {
  const { response } = await handler.handle(request, {
    prefix: "/rpc",
    context: { headers: request.headers },
  });
  return response ?? new Response("Not found", { status: 404 });
};

export const GET = handle;
export const POST = handle;
