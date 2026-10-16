import { savedView } from "@ea/db/schema";
import { decodeId, encodeId, viewSpec } from "@ea/view-spec";
import { fail, redirect } from "@sveltejs/kit";
import { and, desc, eq, sql } from "drizzle-orm";
import { Buffer } from "node:buffer";
import * as v from "valibot";
import { db } from "#lib/server/db/index.js";
import type { Actions, PageServerLoad } from "./$types";

const VISIBILITIES = ["private", "unlisted"] as const;
const VIEW_ID = /^vw_[0-7][0-9a-hjkmnp-tv-z]{25}$/u;
const EXAMPLES = [
  "us/pa/2024-11-05-general/president",
  "us/ga/2020-11-03-general/senate",
  "de/2025-02-23-general/bundestag",
  "tr/2023-05-14-general/president",
  "tr/istanbul/2019-06-23-local/mayor",
  "ie/2024-06-07-european-parliament/dublin",
];

export const load: PageServerLoad = async ({ locals, url }) => {
  if (locals.user == null) {
    redirect(
      303,
      `/sign-in?next=${encodeURIComponent(url.pathname + url.search)}`,
    );
  }
  const requested = url.searchParams.get("view");
  const [mine, opened, examples] = await Promise.all([
    db
      .select({
        id: savedView.id,
        spec: savedView.spec,
        visibility: savedView.visibility,
        updatedAt: savedView.updatedAt,
      })
      .from(savedView)
      .where(eq(savedView.ownerId, locals.user.id))
      .orderBy(desc(savedView.updatedAt)),
    requested == null || !VIEW_ID.test(requested)
      ? []
      : db
          .select({
            id: savedView.id,
            ownerId: savedView.ownerId,
            spec: savedView.spec,
            visibility: savedView.visibility,
          })
          .from(savedView)
          .where(eq(savedView.id, decodeId(requested))),
    db.execute<{
      path: string;
      id: string;
      date: string | null;
      name: string | null;
      election: string | null;
    }>(sql`
      select s.path, s.entity_id as id, el.date_from::text as date,
        (select n.text from name n where n.entity_id = s.entity_id and n.kind in ('official', 'english')
          order by n.is_preferred desc, (n.lang = 'en') desc limit 1) as name,
        (select n.text from name n where n.entity_id = c.election_id and n.kind in ('official', 'english')
          order by n.is_preferred desc, (n.lang = 'en') desc limit 1) as election
      from slug s join contest c on c.id = s.entity_id join election el on el.id = c.election_id
      where s.is_current and s.path = any(${sql.param(EXAMPLES)}::text[])
    `),
  ]);
  const row = opened.find(
    (entry) =>
      entry.ownerId === locals.user?.id || entry.visibility !== "private",
  );
  const stored = row == null ? null : v.safeParse(viewSpec, row.spec);
  const view =
    row == null || stored?.success !== true
      ? null
      : {
          id: encodeId("vw", row.id),
          visibility: row.visibility,
          isOwner: row.ownerId === locals.user.id,
          spec: stored.output,
        };
  const encoded = url.searchParams.get("spec");
  let raw: unknown = null;
  if (encoded != null) {
    try {
      raw = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    } catch {
      raw = null;
    }
  }
  const parsed = v.safeParse(viewSpec, raw);
  return {
    spec: parsed.success ? parsed.output : (view?.spec ?? null),
    view,
    isMissing: requested != null && view == null,
    examples: examples.rows
      .toSorted((a, b) => EXAMPLES.indexOf(a.path) - EXAMPLES.indexOf(b.path))
      .map((row) => ({
        id: row.id,
        name: row.name ?? "",
        election: row.election,
        date: row.date,
      })),
    views: mine.map((entry) => ({
      id: encodeId("vw", entry.id),
      title:
        typeof entry.spec === "object" &&
        entry.spec != null &&
        "title" in entry.spec &&
        typeof entry.spec.title === "string"
          ? entry.spec.title
          : null,
      visibility: entry.visibility,
      updatedAt: entry.updatedAt.toISOString(),
    })),
  };
};

export const actions: Actions = {
  save: async ({ locals, request }) => {
    if (locals.user == null) {
      return fail(401);
    }
    const form = await request.formData();
    let raw: unknown = null;
    try {
      raw = JSON.parse(String(form.get("spec") ?? ""));
    } catch {
      return fail(400);
    }
    const parsed = v.safeParse(viewSpec, raw);
    const visibility = v.safeParse(
      v.picklist(VISIBILITIES),
      form.get("visibility"),
    );
    if (!parsed.success || !visibility.success) {
      return fail(400);
    }
    const id = String(form.get("id") ?? "");
    if (VIEW_ID.test(id)) {
      const updated = await db
        .update(savedView)
        .set({
          spec: parsed.output,
          specVersion: parsed.output.version,
          visibility: visibility.output,
        })
        .where(
          and(
            eq(savedView.id, decodeId(id)),
            eq(savedView.ownerId, locals.user.id),
          ),
        )
        .returning({ id: savedView.id });
      if (updated.length > 0) {
        return { id };
      }
    }
    const [created] = await db
      .insert(savedView)
      .values({
        ownerId: locals.user.id,
        spec: parsed.output,
        specVersion: parsed.output.version,
        visibility: visibility.output,
      })
      .returning({ id: savedView.id });
    return { id: created == null ? null : encodeId("vw", created.id) };
  },
  delete: async ({ locals, request }) => {
    if (locals.user == null) {
      return fail(401);
    }
    const form = await request.formData();
    const id = String(form.get("id") ?? "");
    if (!VIEW_ID.test(id)) {
      return fail(400);
    }
    await db
      .delete(savedView)
      .where(
        and(
          eq(savedView.id, decodeId(id)),
          eq(savedView.ownerId, locals.user.id),
        ),
      );
    return { id: null };
  },
};
