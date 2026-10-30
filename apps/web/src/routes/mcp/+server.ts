import { handle } from "#lib/server/mcp.js";
import type { RequestHandler } from "./$types";

const refuse: RequestHandler = () =>
  new Response(null, { status: 405, headers: { allow: "POST" } });

export const POST: RequestHandler = async ({ request }) => handle(request);
export const GET = refuse;
export const DELETE = refuse;
