import { viewSpec } from "@ea/view-spec";
import { redirect } from "@sveltejs/kit";
import { Buffer } from "node:buffer";
import * as v from "valibot";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = ({ locals, url }) => {
  if (locals.user == null) {
    redirect(
      303,
      `/sign-in?next=${encodeURIComponent(url.pathname + url.search)}`,
    );
  }
  const encoded = url.searchParams.get("spec");
  if (encoded == null) {
    return { spec: null };
  }
  let raw: unknown = null;
  try {
    raw = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
  } catch {
    return { spec: null };
  }
  const parsed = v.safeParse(viewSpec, raw);
  return { spec: parsed.success ? parsed.output : null };
};
