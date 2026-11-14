import skill from "#lib/server/skill.md?raw";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = ({ url }) =>
  new Response(
    skill.replaceAll("{{origin}}", () => url.origin),
    {
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "cache-control": "public, max-age=3600",
      },
    },
  );
