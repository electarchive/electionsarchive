import { profile } from "@ea/db/schema";
import { fail, redirect } from "@sveltejs/kit";
import { eq } from "drizzle-orm";
import * as v from "valibot";
import type { ContributorLink } from "#lib/server/contributors.js";
import { m } from "#lib/paraglide/messages.js";
import { HANDLE } from "#lib/server/contributors.js";
import { authDb } from "#lib/server/db/index.js";
import type { Actions, PageServerLoad } from "./$types";

const RESERVED = new Set([
  "admin",
  "administrator",
  "api",
  "editor",
  "electionsarchive",
  "help",
  "mcp",
  "moderator",
  "official",
  "reviewer",
  "root",
  "staff",
  "support",
  "system",
]);

const Optional = (max: number) =>
  v.pipe(
    v.string(),
    v.normalize("NFC"),
    v.trim(),
    v.maxLength(max),
    v.transform((value) => (value === "" ? null : value)),
  );

const Fields = v.object({
  display_name: Optional(60),
  affiliation: Optional(100),
  bio_md: Optional(1000),
});

function isTaken(error: unknown) {
  for (let cause = error; cause instanceof Error; cause = cause.cause) {
    if ("code" in cause && cause.code === "23505") {
      return true;
    }
  }
  return false;
}

function linksOf(value: string) {
  const links: Array<ContributorLink> = [];
  for (const line of value.split("\n")) {
    const trimmed = line.trim();
    if (trimmed === "") {
      continue;
    }
    const url = URL.parse(trimmed);
    if (url == null || url.protocol !== "https:" || trimmed.length > 300) {
      return null;
    }
    const path = url.pathname === "/" ? "" : url.pathname.replace(/\/$/u, "");
    links.push({
      url: url.href,
      label: `${url.hostname.replace(/^www\./u, "")}${path}`,
    });
  }
  return links.length > 4 ? null : links;
}

export const load: PageServerLoad = async ({ locals, url }) => {
  if (locals.user == null) {
    redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
  }
  const [found] = await authDb
    .select({
      handle: profile.handle,
      displayName: profile.displayName,
      affiliation: profile.affiliation,
      bioMd: profile.bioMd,
      links: profile.links,
      licenseAcceptedAt: profile.licenseAcceptedAt,
    })
    .from(profile)
    .where(eq(profile.userId, locals.user.id));
  const links = v.parse(
    v.array(v.object({ url: v.string(), label: v.string() })),
    found?.links ?? [],
  );
  return {
    handle: found?.handle ?? null,
    displayName: found?.displayName ?? "",
    affiliation: found?.affiliation ?? "",
    bioMd: found?.bioMd ?? "",
    links: links.map((link) => link.url).join("\n"),
    licenseAcceptedAt: found?.licenseAcceptedAt?.toISOString() ?? null,
  };
};

export const actions: Actions = {
  default: async ({ request, locals }) => {
    if (locals.user == null) {
      return fail(401, {
        sent: null,
        field: "form",
        message: m.profile_signed_out(),
      });
    }
    const form = await request.formData();
    const text = (name: string) => String(form.get(name) ?? "");
    const handle = text("handle").trim().toLowerCase();
    const sent = {
      handle,
      displayName: text("display_name"),
      affiliation: text("affiliation"),
      bioMd: text("bio_md"),
      links: text("links"),
    };
    if (!HANDLE.test(handle)) {
      return fail(400, {
        sent,
        field: "handle",
        message: m.username_invalid(),
      });
    }
    if (RESERVED.has(handle)) {
      return fail(409, { sent, field: "handle", message: m.username_taken() });
    }
    const fields = v.safeParse(Fields, {
      display_name: text("display_name"),
      affiliation: text("affiliation"),
      bio_md: text("bio_md"),
    });
    if (!fields.success) {
      return fail(400, {
        sent,
        field: "fields",
        message: m.profile_too_long(),
      });
    }
    const links = linksOf(text("links"));
    if (links == null) {
      return fail(400, {
        sent,
        field: "links",
        message: m.profile_links_invalid(),
      });
    }
    const values = {
      handle,
      displayName: fields.output.display_name,
      affiliation: fields.output.affiliation,
      bioMd: fields.output.bio_md,
      links,
      ...(form.has("license") && { licenseAcceptedAt: new Date() }),
    };
    try {
      await authDb
        .insert(profile)
        .values({ userId: locals.user.id, ...values })
        .onConflictDoUpdate({ target: profile.userId, set: values });
    } catch (error) {
      if (isTaken(error)) {
        return fail(409, {
          sent,
          field: "handle",
          message: m.username_taken(),
        });
      }
      throw error;
    }
    return { saved: handle };
  },
};
