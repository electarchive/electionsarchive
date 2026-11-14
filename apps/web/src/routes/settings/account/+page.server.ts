import { fail, redirect } from "@sveltejs/kit";
import { isAPIError } from "better-auth/api";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { PROJECT } from "#lib/constants.js";
import { m } from "#lib/paraglide/messages.js";
import { allowAccountMail } from "#lib/server/account-mail.js";
import { auth, sendPasswordChanged } from "#lib/server/auth.js";
import { authDb } from "#lib/server/db/index.js";
import { createRateLimiter } from "#lib/server/ratelimit.js";
import type { Actions, PageServerLoad } from "./$types";

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

async function account(userId: string) {
  const { rows } = await authDb.execute<{
    email: string;
    email_verified: boolean;
    has_password: boolean;
    pending_email: string | null;
    is_staff: boolean;
  }>(sql`
    select u.email, u.email_verified,
           exists (select from accounts a where a.user_id = u.id and a.provider_id = 'credential'
                   and a.password is not null) as has_password,
           (select x.value from verifications x where x.identifier = ${pendingKey(userId)}
            and x.expires_at > now() order by x.created_at desc limit 1) as pending_email,
           exists (select from user_role r where r.user_id = u.id) as is_staff
    from users u
    where u.id = ${userId}
  `);
  const found = rows[0];
  if (found == null) {
    redirect(303, "/sign-in?next=%2Fsettings%2Faccount");
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
  return {
    email: found.email,
    emailVerified: found.email_verified,
    pendingEmail: found.pending_email,
    hasPassword: found.has_password,
    staff: found.is_staff,
  };
};

export const actions: Actions = {
  email: async ({ request, locals }) => {
    if (locals.user == null) {
      redirect(303, "/sign-in?next=%2Fsettings%2Faccount");
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
        body: { newEmail: parsed.output, callbackURL: "/settings/account" },
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
      redirect(303, "/sign-in?next=%2Fsettings%2Faccount");
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
      redirect(303, "/sign-in?next=%2Fsettings%2Faccount");
    }
    const form = await request.formData();
    const confirm = String(form.get("confirm") ?? "");
    const problem = (status: number, message: string) =>
      fail(status, { form: "delete" as const, message });
    const { email, has_password: hasPassword } = await account(locals.user.id);
    if (!hasPassword && confirm.trim().toLowerCase() !== email.toLowerCase()) {
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
