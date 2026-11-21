import { issue, KEPT, VISITOR, visitor } from "#lib/server/visitor.js";
import type { RequestHandler } from "./$types";

export const POST: RequestHandler = ({ cookies, request }) => {
  const value =
    visitor(request.headers.get("cookie")).status === "valid" ? null : issue();
  if (value != null) {
    cookies.set(VISITOR, value, {
      path: "/",
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      maxAge: KEPT,
    });
  }
  return new Response(null, {
    status: 204,
    headers: { "cache-control": "no-store" },
  });
};
