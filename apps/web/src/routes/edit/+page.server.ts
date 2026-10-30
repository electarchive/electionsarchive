import { error, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { blank, templates, templateTitle } from "#lib/workspace.js";
import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, url, setHeaders }) => {
  if (locals.user == null) {
    redirect(
      303,
      `/sign-in?next=${encodeURIComponent(url.pathname + url.search)}`,
    );
  }
  setHeaders({ "cache-control": "no-store" });
  const { rows } = await db.execute<{
    number: number;
    title: string;
    rows: number;
    updated_at: string;
  }>(sql`
    select p.number, p.title,
           coalesce(jsonb_array_length(p.draft -> 'workspace' -> 'rows'), (
             select sum(jsonb_array_length(c -> 'rows'))
             from jsonb_array_elements(p.draft -> 'workspace' -> 'contests') c
           ), 0)::int as rows,
           p.updated_at::text
    from proposal p
    where p.author_user_id = ${locals.user.id} and p.state = 'draft'
      and p.draft ? 'workspace'
      and not exists (select from proposal_revision r where r.proposal_id = p.id)
    order by p.updated_at desc
    limit 50
  `);
  return { drafts: rows, record: url.searchParams.get("record") ?? "" };
};

export const actions: Actions = {
  start: async ({ locals, url, request }) => {
    if (locals.user == null) {
      redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
    }
    const form = await request.formData();
    const template = templates.find((item) => item === form.get("template"));
    if (template == null) {
      error(400);
    }
    const given = form.get("record");
    const record = typeof given === "string" ? given.trim().slice(0, 2048) : "";
    const summary = templateTitle[template]();
    const draft = {
      summary,
      ops: [],
      workspace:
        template === "results"
          ? {
              template,
              contests:
                record === ""
                  ? []
                  : [
                      {
                        record,
                        stage: "certified",
                        columns: [],
                        rows: [],
                        printed: {},
                        outcomes: {},
                      },
                    ],
              evidence: null,
            }
          : {
              template,
              rows: record === "" ? [] : [{ ...blank(template), record }],
            },
    };
    const { rows } = await db.execute<{ number: number }>(sql`
      insert into proposal (author_user_id, title, draft)
      values (${locals.user.id}, ${summary}, ${JSON.stringify(draft)}::jsonb)
      returning number
    `);
    redirect(303, `/edit/${rows[0]?.number}`);
  },
};
