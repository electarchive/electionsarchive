import { sql } from "drizzle-orm";
import {
  boolean,
  index,
  snakeCase,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";

const primaryId = () =>
  uuid()
    .primaryKey()
    .default(sql`uuidv7()`);

const time = () => timestamp({ withTimezone: true, precision: 3 });

const timeUpdated = () =>
  time()
    .notNull()
    .defaultNow()
    .$onUpdate(() => new Date());

export const users = snakeCase.table(
  "users",
  {
    id: primaryId(),
    email: text().notNull(),
    emailVerified: boolean().notNull().default(false),
    name: text().notNull(),
    image: text(),
    role: text(),
    banned: boolean().notNull().default(false),
    banReason: text(),
    banExpires: time(),
    createdAt: time().notNull().defaultNow(),
    updatedAt: timeUpdated(),
  },
  (t) => [uniqueIndex("users_email_key").on(t.email)],
);

export const accounts = snakeCase.table(
  "accounts",
  {
    id: primaryId(),
    accountId: text().notNull(),
    providerId: text().notNull(),
    userId: uuid()
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    accessToken: text(),
    refreshToken: text(),
    idToken: text(),
    accessTokenExpiresAt: time(),
    refreshTokenExpiresAt: time(),
    scope: text(),
    password: text(),
    createdAt: time().notNull().defaultNow(),
    updatedAt: timeUpdated(),
  },
  (t) => [
    index("accounts_user_id_idx").on(t.userId),
    uniqueIndex("accounts_provider_id_account_id_key").on(
      t.providerId,
      t.accountId,
    ),
  ],
);

export const sessions = snakeCase.table(
  "sessions",
  {
    id: uuid().primaryKey(),
    token: text().notNull(),
    userId: uuid()
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    expiresAt: time().notNull(),
    ipAddress: text(),
    userAgent: text(),
    impersonatedBy: uuid().references(() => users.id, { onDelete: "set null" }),
    createdAt: time().notNull().defaultNow(),
    updatedAt: timeUpdated(),
  },
  (t) => [
    uniqueIndex("sessions_token_key").on(t.token),
    index("sessions_user_id_idx").on(t.userId),
    index("sessions_expires_at_idx").on(t.expiresAt),
  ],
);

export const verifications = snakeCase.table(
  "verifications",
  {
    id: primaryId(),
    identifier: text().notNull(),
    value: text().notNull(),
    expiresAt: time().notNull(),
    createdAt: time().notNull().defaultNow(),
    updatedAt: timeUpdated(),
  },
  (t) => [
    index("verifications_identifier_idx").on(t.identifier),
    index("verifications_expires_at_idx").on(t.expiresAt),
  ],
);
