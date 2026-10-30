import { dev } from "$app/env";
import {
  BETTER_AUTH_SECRET,
  BETTER_AUTH_URL,
  GITHUB_CLIENT_ID,
  GITHUB_CLIENT_SECRET,
  GOOGLE_CLIENT_ID,
  GOOGLE_CLIENT_SECRET,
  RESEND_API_KEY,
} from "$app/env/private";
import { getRequestEvent } from "$app/server";
import { apiKey } from "@better-auth/api-key";
import { drizzleAdapter } from "@better-auth/drizzle-adapter";
import { i18n } from "@better-auth/i18n";
import * as schema from "@ea/db/schema";
import { betterAuth } from "better-auth/minimal";
import { admin } from "better-auth/plugins";
import { sveltekitCookies } from "better-auth/svelte-kit";
import { randomUUID } from "node:crypto";
import { Resend } from "resend";
import { EMAIL_FROM, PROJECT } from "#lib/constants.js";
import { m } from "#lib/paraglide/messages.js";
import { baseLocale, getLocale, locales } from "#lib/paraglide/runtime.js";
import { db } from "#lib/server/db/index.js";
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

const resend = new Resend(RESEND_API_KEY);

async function sendEmail({
  to,
  subject,
  body,
  action,
  url,
  category,
}: {
  to: string;
  subject: string;
  body: string;
  action: string;
  url: string;
  category: string;
}) {
  const { error } = await resend.emails.send({
    from: EMAIL_FROM,
    to: [to],
    subject,
    html: `<p>${body}</p><p><a href="${url}">${action}</a></p>`,
    text: `${body}\n\n${url}`,
    tags: [{ name: "category", value: category }],
  });

  if (error != null) {
    throw new Error(error.message);
  }
}

const rateLimit = createRateLimiter({
  prefix: `${PROJECT}:auth`,
  penalty: null,
});

export const auth = betterAuth({
  database: drizzleAdapter(db, {
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
  emailAndPassword: {
    enabled: true,
    sendResetPassword: async ({ user, url }) => {
      await sendEmail({
        to: user.email,
        subject: m.reset_password_subject(),
        body: m.reset_password_body(),
        action: m.reset_password_action(),
        url,
        category: "password_reset",
      });
    },
  },
  emailVerification: {
    sendVerificationEmail: async ({ user, url }) => {
      await sendEmail({
        to: user.email,
        subject: m.verify_email_subject(),
        body: m.verify_email_body(),
        action: m.verify_email_action(),
        url,
        category: "confirm_email",
      });
    },
  },
  user: {
    changeEmail: {
      enabled: true,
      sendChangeEmailConfirmation: async ({ newEmail, url }) => {
        await sendEmail({
          to: newEmail,
          subject: m.change_email_subject(),
          body: m.change_email_body(),
          action: m.change_email_action(),
          url,
          category: "change_email",
        });
      },
    },
  },
});
