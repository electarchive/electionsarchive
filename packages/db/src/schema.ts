import { sql } from "drizzle-orm";
import {
  bigint,
  boolean,
  index,
  integer,
  jsonb,
  pgEnum,
  snakeCase,
  text,
  timestamp,
  unique,
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

export const apikeys = snakeCase.table(
  "apikeys",
  {
    id: primaryId(),
    configId: text().notNull().default("default"),
    name: text(),
    start: text(),
    referenceId: uuid()
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    prefix: text(),
    key: text().notNull(),
    refillInterval: bigint({ mode: "number" }),
    refillAmount: integer(),
    lastRefillAt: time(),
    enabled: boolean().default(true),
    rateLimitEnabled: boolean().default(true),
    rateLimitTimeWindow: bigint({ mode: "number" }),
    rateLimitMax: integer(),
    requestCount: integer().default(0),
    remaining: integer(),
    lastRequest: time(),
    expiresAt: time(),
    permissions: text(),
    metadata: text(),
    createdAt: time().notNull().defaultNow(),
    updatedAt: timeUpdated(),
  },
  (t) => [
    uniqueIndex("apikeys_key_key").on(t.key),
    index("apikeys_config_id_idx").on(t.configId),
    index("apikeys_reference_id_idx").on(t.referenceId),
  ],
);

export const appRole = pgEnum("app_role", ["admin", "reviewer"]);

export const userRole = snakeCase.table(
  "user_role",
  {
    id: primaryId(),
    userId: uuid()
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    role: appRole().notNull(),
    polityScopeId: uuid(),
    createdAt: time().notNull().defaultNow(),
  },
  (t) => [
    unique("user_role_user_id_role_polity_scope_id_key")
      .on(t.userId, t.role, t.polityScopeId)
      .nullsNotDistinct(),
  ],
);

export const viewVisibility = pgEnum("view_visibility", [
  "private",
  "unlisted",
  "public",
]);

export const savedView = snakeCase.table(
  "saved_view",
  {
    id: primaryId(),
    ownerId: uuid()
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    spec: jsonb().notNull(),
    specVersion: integer().notNull(),
    visibility: viewVisibility().notNull().default("private"),
    slug: text(),
    publishedAt: time(),
    createdAt: time().notNull().defaultNow(),
    updatedAt: timeUpdated(),
  },
  (t) => [
    uniqueIndex("saved_view_slug_key").on(t.slug),
    index("saved_view_owner_id_idx").on(t.ownerId),
  ],
);
