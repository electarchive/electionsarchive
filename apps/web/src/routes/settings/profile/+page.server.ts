import { READ_ONLY } from "$app/env/public";
import { profile } from "@ea/db/schema";
import { fail, redirect } from "@sveltejs/kit";
import { isAPIError } from "better-auth/api";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { ContributorLink } from "#lib/server/contributors.js";
import { PROJECT } from "#lib/constants.js";
import { parseMarkdown } from "#lib/markdown-tree.js";
import { m } from "#lib/paraglide/messages.js";
import { allowAccountMail } from "#lib/server/account-mail.js";
import { auth, sendPasswordChanged } from "#lib/server/auth.js";
import { credits } from "#lib/server/bounties.js";
import { roles } from "#lib/server/contrib.js";
import { contributor, HANDLE } from "#lib/server/contributors.js";
import { authDb } from "#lib/server/db/index.js";
import { createRateLimiter } from "#lib/server/ratelimit.js";
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

const Email = v.pipe(
  v.string(),
  v.trim(),
  v.toLowerCase(),
  v.maxLength(254),
  v.email(),
);

const limiter = createRateLimiter({
  prefix: `${PROJECT}:account`,
  penalty: null,
});

const allowAttempt = async (name: string, userId: string) =>
  (await limiter({ name, rules: [{ limit: 5, window: 600 }] }, userId))
    .isAllowed;

const pendingKey = (userId: string) => `change-email:${userId}`;

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

async function account(userId: string) {
  const { rows } = await authDb.execute<{
    email: string;
    email_verified: boolean;
    member_since: string;
    has_password: boolean;
    pending_email: string | null;
    handle: string | null;
    display_name: string | null;
    affiliation: string | null;
    bio_md: string | null;
    links: unknown;
    license_accepted_at: string | null;
  }>(sql`
    select u.email, u.email_verified, (u.created_at at time zone 'UTC')::date::text as member_since,
           exists (select from accounts a where a.user_id = u.id and a.provider_id = 'credential'
                   and a.password is not null) as has_password,
           (select x.value from verifications x where x.identifier = ${pendingKey(userId)}
            and x.expires_at > now() order by x.created_at desc limit 1) as pending_email,
           p.handle, p.display_name, p.affiliation, p.bio_md, p.links,
           to_char(p.license_accepted_at at time zone 'UTC', 'YYYY-MM-DD') as license_accepted_at
    from users u left join profile p on p.user_id = u.id
    where u.id = ${userId}
  `);
  const found = rows[0];
  if (found == null) {
    redirect(303, "/sign-in?next=%2Fsettings%2Fprofile");
  }
  return found;
}

async function setPending(userId: string, email: string | null) {
  const context = await auth.$context;
  await context.internalAdapter.deleteVerificationByIdentifier(
    pendingKey(userId),
  );
  if (email != null) {
    await context.internalAdapter.createVerificationValue({
      identifier: pendingKey(userId),
      value: email,
      expiresAt: new Date(Date.now() + 3_600_000),
    });
  }
}

export const load: PageServerLoad = async ({ locals, url }) => {
  if (locals.user == null) {
    redirect(
      303,
      `/sign-in?next=${encodeURIComponent(url.pathname + url.search)}`,
    );
  }
  const found = await account(locals.user.id);
  if (found.pending_email === found.email) {
    await setPending(locals.user.id, null);
    found.pending_email = null;
  }
  const links = v.parse(
    v.array(v.object({ url: v.string(), label: v.string() })),
    found.links ?? [],
  );
  const [work, staff] = await Promise.all([
    found.handle == null || READ_ONLY
      ? null
      : Promise.all([contributor(found.handle), credits(locals.user.id)]),
    roles(locals.user.id),
  ]);
  return {
    view:
      found.handle == null
        ? null
        : {
            profile: work?.[0].profile ?? {
              handle: found.handle,
              display_name: found.display_name,
              affiliation: found.affiliation,
              links,
              member_since: found.member_since,
              level: null,
              limited: false,
            },
            bio: found.bio_md == null ? null : parseMarkdown(found.bio_md),
            work:
              work == null
                ? null
                : {
                    counts: work[0].counts,
                    proposals: work[0].proposals,
                    agents: work[0].agents,
                    bounties: work[1],
                  },
          },
    handle: found.handle,
    displayName: found.display_name ?? "",
    affiliation: found.affiliation ?? "",
    bioMd: found.bio_md ?? "",
    links: links.map((link) => link.url).join("\n"),
    licenseAcceptedAt: found.license_accepted_at,
    email: found.email,
    emailVerified: found.email_verified,
    pendingEmail: found.pending_email,
    hasPassword: found.has_password,
    staff: staff.admin || staff.reviewer,
  };
};

export const actions: Actions = {
  profile: async ({ request, locals }) => {
    if (locals.user == null) {
      return fail(401, {
        form: "profile" as const,
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
    const problem = (status: number, field: string, message: string) =>
      fail(status, { form: "profile" as const, sent, field, message });
    if (!HANDLE.test(handle)) {
      return problem(400, "handle", m.username_invalid());
    }
    if (RESERVED.has(handle)) {
      return problem(409, "handle", m.username_taken());
    }
    const fields = v.safeParse(Fields, {
      display_name: sent.displayName,
      affiliation: sent.affiliation,
      bio_md: sent.bioMd,
    });
    if (!fields.success) {
      return problem(400, "fields", m.profile_too_long());
    }
    const links = linksOf(sent.links);
    if (links == null) {
      return problem(400, "links", m.profile_links_invalid());
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
        return problem(409, "handle", m.username_taken());
      }
      throw error;
    }
    redirect(303, "/settings/profile");
  },

  email: async ({ request, locals }) => {
    if (locals.user == null) {
      redirect(303, "/sign-in?next=%2Fsettings%2Fprofile%3Fedit");
    }
    const form = await request.formData();
    const sent = String(form.get("email") ?? "");
    const problem = (status: number, message: string) =>
      fail(status, { form: "email" as const, sent, message });
    const parsed = v.safeParse(Email, sent);
    if (!parsed.success) {
      return problem(400, m.email_invalid());
    }
    const { email } = await account(locals.user.id);
    if (parsed.output === email.toLowerCase()) {
      return problem(400, m.email_same());
    }
    if (!(await allowAccountMail("change-email", locals.user.id))) {
      return problem(429, m.verify_page_wait());
    }
    try {
      await auth.api.changeEmail({
        body: {
          newEmail: parsed.output,
          callbackURL: "/settings/profile?edit",
        },
        headers: request.headers,
      });
    } catch (error) {
      if (isAPIError(error)) {
        return problem(400, error.message);
      }
      throw error;
    }
    await setPending(locals.user.id, parsed.output);
    return { form: "email" as const, done: true };
  },

  password: async ({ request, locals }) => {
    if (locals.user == null) {
      redirect(303, "/sign-in?next=%2Fsettings%2Fprofile%3Fedit");
    }
    const form = await request.formData();
    const text = (name: string) => String(form.get(name) ?? "");
    const current = text("current");
    const next = text("password");
    const revoke = form.has("revoke");
    const problem = (status: number, field: string, message: string) =>
      fail(status, { form: "password" as const, field, message });
    const { email, has_password: hasPassword } = await account(locals.user.id);
    if (hasPassword && current === "") {
      return problem(400, "current", m.password_current_missing());
    }
    if (next.length < 8) {
      return problem(400, "password", m.PASSWORD_TOO_SHORT());
    }
    if (next.length > 128) {
      return problem(400, "password", m.PASSWORD_TOO_LONG());
    }
    if (next.trim().toLowerCase() === email.toLowerCase()) {
      return problem(400, "password", m.password_is_email());
    }
    if (hasPassword && next === current) {
      return problem(400, "password", m.password_same());
    }
    if (next !== text("confirm")) {
      return problem(400, "confirm", m.password_mismatch());
    }
    if (!(await allowAttempt("password", locals.user.id))) {
      return problem(429, "form", m.account_too_many_attempts());
    }
    try {
      if (hasPassword) {
        await auth.api.changePassword({
          body: {
            currentPassword: current,
            newPassword: next,
            revokeOtherSessions: revoke,
          },
          headers: request.headers,
        });
      } else {
        await auth.api.setPassword({
          body: { newPassword: next },
          headers: request.headers,
        });
        if (revoke) {
          await auth.api.revokeOtherSessions({ headers: request.headers });
        }
        await sendPasswordChanged(email);
      }
    } catch (error) {
      if (isAPIError(error)) {
        return error.body?.code === "INVALID_PASSWORD"
          ? problem(400, "current", m.password_current_wrong())
          : problem(400, "form", error.message);
      }
      throw error;
    }
    return {
      form: "password" as const,
      done: hasPassword ? m.password_changed() : m.password_set_done(),
    };
  },

  delete: async ({ request, locals }) => {
    if (locals.user == null) {
      redirect(303, "/sign-in?next=%2Fsettings%2Fprofile%3Fedit");
    }
    const form = await request.formData();
    const confirm = String(form.get("confirm") ?? "");
    const problem = (status: number, message: string) =>
      fail(status, { form: "delete" as const, message });
    const { email, has_password: hasPassword } = await account(locals.user.id);
    if (
      !hasPassword &&
      confirm.trim().toLowerCase() !== email.toLowerCase()
    ) {
      return problem(400, m.delete_account_mismatch());
    }
    if (hasPassword && confirm === "") {
      return problem(400, m.password_current_missing());
    }
    if (!(await allowAttempt("delete", locals.user.id))) {
      return problem(429, m.account_too_many_attempts());
    }
    try {
      await auth.api.deleteUser({
        body: hasPassword ? { password: confirm } : {},
        headers: request.headers,
      });
    } catch (error) {
      if (isAPIError(error)) {
        const code = error.body?.code;
        if (code === "INVALID_PASSWORD") {
          return problem(400, m.password_current_wrong());
        }
        if (code === "SESSION_EXPIRED") {
          return problem(403, m.delete_account_stale());
        }
        return problem(400, error.message);
      }
      throw error;
    }
    await setPending(locals.user.id, null);
    redirect(303, "/");
  },
};
