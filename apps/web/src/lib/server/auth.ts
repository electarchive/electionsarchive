import { dev } from "$app/env";
import {
  BETTER_AUTH_SECRET,
  BETTER_AUTH_URL,
  GITHUB_CLIENT_ID,
  GITHUB_CLIENT_SECRET,
  GOOGLE_CLIENT_ID,
  GOOGLE_CLIENT_SECRET,
} from "$app/env/private";
import { READ_ONLY } from "$app/env/public";
import { getRequestEvent } from "$app/server";
import { apiKey } from "@better-auth/api-key";
import { drizzleAdapter } from "@better-auth/drizzle-adapter";
import { i18n } from "@better-auth/i18n";
import * as schema from "@ea/db/schema";
import { APIError, createAuthMiddleware, isAPIError } from "better-auth/api";
import { betterAuth } from "better-auth/minimal";
import { admin } from "better-auth/plugins";
import { sveltekitCookies } from "better-auth/svelte-kit";
import { sql } from "drizzle-orm";
import { randomUUID } from "node:crypto";
import { PROJECT } from "#lib/constants.js";
import { m } from "#lib/paraglide/messages.js";
import { baseLocale, getLocale, locales } from "#lib/paraglide/runtime.js";
import { authDb, db } from "#lib/server/db/index.js";
import { sendEmail } from "#lib/server/email.js";
import { createRateLimiter } from "#lib/server/ratelimit.js";

const authErrorMessages = {
  CREDENTIAL_ACCOUNT_NOT_FOUND: m.CREDENTIAL_ACCOUNT_NOT_FOUND,
  EMAIL_CAN_NOT_BE_UPDATED: m.EMAIL_CAN_NOT_BE_UPDATED,
  EMAIL_NOT_VERIFIED: m.EMAIL_NOT_VERIFIED,
  FAILED_TO_CREATE_SESSION: m.FAILED_TO_CREATE_SESSION,
  FAILED_TO_GET_SESSION: m.FAILED_TO_GET_SESSION,
  FAILED_TO_GET_USER_INFO: m.FAILED_TO_GET_USER_INFO,
  FAILED_TO_UPDATE_USER: m.FAILED_TO_UPDATE_USER,
  INVALID_EMAIL: m.INVALID_EMAIL,
  INVALID_EMAIL_OR_PASSWORD: m.INVALID_EMAIL_OR_PASSWORD,
  INVALID_PASSWORD: m.INVALID_PASSWORD,
  INVALID_TOKEN: m.INVALID_TOKEN,
  TOKEN_EXPIRED: m.INVALID_TOKEN,
  PASSWORD_TOO_LONG: m.PASSWORD_TOO_LONG,
  PASSWORD_TOO_SHORT: m.PASSWORD_TOO_SHORT,
  SESSION_EXPIRED: m.SESSION_EXPIRED,
  USER_ALREADY_EXISTS: m.USER_ALREADY_EXISTS,
  USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL:
    m.USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL,
  USER_ALREADY_HAS_PASSWORD: m.USER_ALREADY_HAS_PASSWORD,
  USER_EMAIL_NOT_FOUND: m.USER_EMAIL_NOT_FOUND,
  USER_NOT_FOUND: m.USER_NOT_FOUND,
};

const translations: Record<string, Record<string, string>> = {};
for (const locale of locales) {
  const dictionary: Record<string, string> = {};
  for (const [code, getMessage] of Object.entries(authErrorMessages)) {
    dictionary[code] = getMessage({}, { locale });
  }
  translations[locale] = dictionary;
}

const link = (path: string) => new URL(path, BETTER_AUTH_URL).href;

export const sendPasswordChanged = async (email: string) => {
  await sendEmail(email, {
    subject: m.password_changed_subject(),
    preheader: m.password_changed_preheader(),
    heading: m.password_changed_heading(),
    body: m.password_changed_body({ email }),
    action: {
      label: m.password_changed_action(),
      url: link("/forgot-password"),
    },
    reason: m.password_changed_reason(),
  });
};

const rateLimit = createRateLimiter({
  prefix: `${PROJECT}:auth`,
  penalty: null,
});

export const auth = betterAuth({
  database: drizzleAdapter(authDb, {
    provider: "pg",
    usePlural: true,
    transaction: true,
    schema,
  }),
  secret: BETTER_AUTH_SECRET,
  baseURL: BETTER_AUTH_URL,
  trustedOrigins: dev
    ? (request) => [
        BETTER_AUTH_URL,
        request == null ? null : new URL(request.url).origin,
      ]
    : [BETTER_AUTH_URL],
  telemetry: { enabled: false },
  advanced: {
    database: {
      generateId: ({ model }) =>
        model === "session" || model === "apikey" ? randomUUID() : false,
    },
  },
  rateLimit: {
    customRules: {
      "/send-verification-email": { window: 60, max: 1 },
      "/request-password-reset": { window: 60, max: 1 },
      "/change-email": { window: 60, max: 1 },
      "/change-password": { window: 600, max: 5 },
      "/delete-user": { window: 600, max: 5 },
    },
    customStorage: {
      consume: async (key, rule) => {
        const { isAllowed, retryAfter } = await rateLimit(
          { name: "auth", rules: [{ limit: rule.max, window: rule.window }] },
          key,
        );
        return {
          allowed: isAllowed,
          retryAfter: isAllowed ? null : retryAfter,
        };
      },
    },
  },
  session: { cookieCache: { enabled: true, maxAge: 5 * 60 } },
  socialProviders: {
    github: {
      enabled: GITHUB_CLIENT_ID != null && GITHUB_CLIENT_SECRET != null,
      clientId: GITHUB_CLIENT_ID ?? "",
      clientSecret: GITHUB_CLIENT_SECRET ?? "",
    },
    google: {
      enabled: GOOGLE_CLIENT_ID != null && GOOGLE_CLIENT_SECRET != null,
      clientId: GOOGLE_CLIENT_ID ?? "",
      clientSecret: GOOGLE_CLIENT_SECRET ?? "",
    },
  },
  plugins: [
    admin(),
    apiKey({
      defaultPrefix: "ea_",
      enableMetadata: true,
      rateLimit: { enabled: false },
    }),
    i18n({
      translations,
      defaultLocale: baseLocale,
      detection: ["callback"],
      getLocale: () => getLocale(),
    }),
    sveltekitCookies(getRequestEvent),
  ],
  hooks: {
    after: createAuthMiddleware(async (ctx) => {
      const email = ctx.context.session?.user.email;
      if (
        ctx.path === "/change-password" &&
        email != null &&
        !isAPIError(ctx.context.returned)
      ) {
        await sendPasswordChanged(email);
      }
    }),
  },
  emailAndPassword: {
    enabled: true,
    sendResetPassword: async ({ user, token }) => {
      await sendEmail(user.email, {
        subject: m.reset_password_subject(),
        preheader: m.reset_password_preheader(),
        heading: m.reset_password_heading(),
        body: m.reset_password_body({ email: user.email }),
        action: {
          label: m.reset_password_action(),
          url: link(`/reset-password?token=${encodeURIComponent(token)}`),
          note: m.email_link_expiry(),
        },
        reason: m.reset_password_reason(),
      });
    },
    onPasswordReset: async ({ user }) => {
      await sendPasswordChanged(user.email);
    },
  },
  emailVerification: {
    sendOnSignUp: true,
    autoSignInAfterVerification: true,
    sendVerificationEmail: async ({ user, url }) => {
      await sendEmail(user.email, {
        subject: m.verify_email_subject(),
        preheader: m.verify_email_preheader(),
        heading: m.verify_email_heading(),
        body: m.verify_email_body({ email: user.email }),
        action: {
          label: m.verify_email_action(),
          url,
          note: m.email_link_expiry(),
        },
        reason: m.verify_email_reason({ email: user.email }),
      });
    },
  },
  user: {
    deleteUser: {
      enabled: true,
      beforeDelete: async (user) => {
        const roles = await authDb.execute<{ role: string }>(
          sql`select role from user_role where user_id = ${user.id}`,
        );
        const limits = READ_ONLY
          ? null
          : await db.execute<{ id: string }>(sql`
            select id from account_limit where set_by = ${user.id} or lifted_by = ${user.id} limit 1
          `);
        if (roles.rows.length > 0 || (limits?.rows.length ?? 0) > 0) {
          throw APIError.fromStatus("FORBIDDEN", {
            code: "STAFF_ACCOUNT",
            message: m.delete_account_staff(),
          });
        }
        if (!READ_ONLY) {
          await db.execute(sql`
            delete from proposal p
            where p.author_user_id = ${user.id} and p.submitted_at is null
              and not exists (select from bounty b where b.fulfilled_by_proposal = p.id)
          `);
        }
      },
    },
    changeEmail: {
      enabled: true,
      sendChangeEmailConfirmation: async ({ user, newEmail, url }) => {
        await sendEmail(user.email, {
          subject: m.change_email_subject(),
          preheader: m.change_email_preheader({ newEmail }),
          heading: m.change_email_heading(),
          body: m.change_email_body({ email: user.email, newEmail }),
          action: {
            label: m.change_email_action(),
            url,
            note: m.email_link_expiry(),
          },
          reason: m.change_email_reason({ email: user.email }),
        });
      },
    },
  },
});
