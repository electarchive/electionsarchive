import { sql } from "drizzle-orm";
import {
  bigint,
  boolean,
  check,
  index,
  integer,
  jsonb,
  pgEnum,
  primaryKey,
  smallint,
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

export const profile = snakeCase.table("profile", {
  userId: uuid()
    .primaryKey()
    .references(() => users.id, { onDelete: "cascade" }),
  handle: text().notNull().unique(),
  displayName: text(),
  bioMd: text(),
  affiliation: text(),
  links: jsonb().notNull().default([]),
  licenseAcceptedAt: time(),
  feedToken: text().unique(),
  createdAt: time().notNull().defaultNow(),
});

export const proposalState = pgEnum("proposal_state", [
  "draft",
  "open",
  "changes_requested",
  "approved",
  "published",
  "rejected",
  "withdrawn",
  "reverted",
]);

export const proposal = snakeCase.table(
  "proposal",
  {
    id: primaryId(),
    number: integer()
      .notNull()
      .unique()
      .generatedAlwaysAsIdentity({ startWith: 1000 }),
    authorUserId: uuid().references(() => users.id, { onDelete: "set null" }),
    agentKeyId: uuid().references(() => apikeys.id, { onDelete: "set null" }),
    agentName: text(),
    agentModel: text(),
    title: text().notNull(),
    polityId: uuid(),
    state: proposalState().notNull().default("draft"),
    tier: smallint(),
    headChangesetId: uuid(),
    draft: jsonb(),
    prescreen: jsonb(),
    prereview: jsonb(),
    decidedBy: uuid().references(() => users.id, { onDelete: "set null" }),
    decidedAt: time(),
    decisionReason: text(),
    createdAt: time().notNull().defaultNow(),
    updatedAt: timeUpdated(),
    submittedAt: time(),
  },
  (t) => [
    index("proposal_author_user_id_idx").on(t.authorUserId),
    index("proposal_state_tier_idx").on(t.state, t.tier),
    index("proposal_polity_id_idx").on(t.polityId),
    check("proposal_tier_check", sql`${t.tier} between 1 and 3`),
  ],
);

export const proposalRevision = snakeCase.table(
  "proposal_revision",
  {
    proposalId: uuid()
      .notNull()
      .references(() => proposal.id, { onDelete: "cascade" }),
    n: integer().notNull(),
    changesetId: uuid(),
    payload: jsonb().notNull(),
    diff: jsonb(),
    checks: jsonb(),
    note: text(),
    createdAt: time().notNull().defaultNow(),
  },
  (t) => [
    primaryKey({ columns: [t.proposalId, t.n] }),
    uniqueIndex("proposal_revision_changeset_id_key").on(t.changesetId),
  ],
);

export const proposalJobKind = pgEnum("proposal_job_kind", [
  "check",
  "submit",
  "publish",
  "reject",
  "revert",
  "withdraw",
]);

export const proposalJobStatus = pgEnum("proposal_job_status", [
  "queued",
  "running",
  "done",
  "failed",
]);

export const proposalJob = snakeCase.table(
  "proposal_job",
  {
    id: primaryId(),
    proposalId: uuid()
      .notNull()
      .references(() => proposal.id, { onDelete: "cascade" }),
    revisionN: integer(),
    kind: proposalJobKind().notNull(),
    status: proposalJobStatus().notNull().default("queued"),
    requestedBy: uuid().references(() => users.id, { onDelete: "set null" }),
    createdAt: time().notNull().defaultNow(),
    startedAt: time(),
    finishedAt: time(),
    output: jsonb(),
  },
  (t) => [
    index("proposal_job_queued_idx")
      .on(t.createdAt)
      .where(sql`${t.status} = 'queued'`),
    index("proposal_job_proposal_id_idx").on(t.proposalId),
  ],
);

export const activityKind = pgEnum("activity_kind", [
  "proposal_submitted",
  "proposal_revised",
  "changes_requested",
  "proposal_rejected",
  "proposal_withdrawn",
  "proposal_published",
  "proposal_reverted",
  "changeset_published",
]);

export const activity = snakeCase.table(
  "activity",
  {
    id: primaryId(),
    at: time().notNull().defaultNow(),
    actorUserId: uuid().references(() => users.id, { onDelete: "set null" }),
    agentKeyId: uuid().references(() => apikeys.id, { onDelete: "set null" }),
    kind: activityKind().notNull(),
    proposalId: uuid().references(() => proposal.id, { onDelete: "cascade" }),
    changesetId: uuid(),
    entityIds: uuid()
      .array()
      .notNull()
      .default(sql`'{}'`),
    polityIds: uuid()
      .array()
      .notNull()
      .default(sql`'{}'`),
    summary: text().notNull(),
  },
  (t) => [
    index("activity_at_idx").on(t.at.desc()),
    index("activity_actor_user_id_at_idx").on(t.actorUserId, t.at.desc()),
    index("activity_proposal_id_idx").on(t.proposalId),
    index("activity_entity_ids_idx").using("gin", t.entityIds),
    index("activity_polity_ids_idx").using("gin", t.polityIds),
  ],
);

export const archiveCaptureStatus = pgEnum("archive_capture_status", [
  "captured",
  "blocked",
  "failed",
]);

export const archiveCapture = snakeCase.table(
  "archive_capture",
  {
    id: primaryId(),
    ownerUserId: uuid().references(() => users.id, { onDelete: "set null" }),
    url: text().notNull(),
    finalUrl: text(),
    waybackUrl: text(),
    status: archiveCaptureStatus().notNull(),
    error: text(),
    rawSha256: text(),
    rawKey: text(),
    mediaType: text(),
    bytes: bigint({ mode: "number" }),
    capturedAt: time().notNull().defaultNow(),
  },
  (t) => [
    check(
      "archive_capture_sha256_check",
      sql`${t.rawSha256} ~ '^[0-9a-f]{64}$'`,
    ),
    check(
      "archive_capture_raw_check",
      sql`(${t.status} = 'captured') = (${t.rawSha256} is not null and ${t.rawKey} is not null)`,
    ),
    index("archive_capture_owner_idx").on(t.ownerUserId, t.capturedAt.desc()),
    index("archive_capture_sha256_idx").on(t.rawSha256),
  ],
);

export const uploadStatus = pgEnum("upload_status", [
  "received",
  "ready",
  "rejected",
]);

export const upload = snakeCase.table(
  "upload",
  {
    id: primaryId(),
    ownerUserId: uuid().references(() => users.id, { onDelete: "set null" }),
    agentKeyId: uuid().references(() => apikeys.id, { onDelete: "set null" }),
    sha256: text().notNull(),
    mediaType: text().notNull(),
    bytes: bigint({ mode: "number" }).notNull(),
    filename: text().notNull(),
    storageKey: text(),
    pages: integer(),
    status: uploadStatus().notNull().default("received"),
    error: text(),
    publicRecord: boolean().notNull().default(false),
    createdAt: time().notNull().defaultNow(),
  },
  (t) => [
    check("upload_sha256_check", sql`${t.sha256} ~ '^[0-9a-f]{64}$'`),
    check(
      "upload_bytes_check",
      sql`${t.bytes} > 0 and ${t.bytes} <= 209715200`,
    ),
    check(
      "upload_ready_check",
      sql`${t.status} <> 'ready' or ${t.storageKey} is not null`,
    ),
    uniqueIndex("upload_owner_sha256_idx").on(t.ownerUserId, t.sha256),
    index("upload_sha256_idx").on(t.sha256),
  ],
);

export const uploadPage = snakeCase.table(
  "upload_page",
  {
    uploadId: uuid()
      .notNull()
      .references(() => upload.id, { onDelete: "cascade" }),
    pageNumber: integer().notNull(),
    imageKey: text().notNull(),
    width: integer().notNull(),
    height: integer().notNull(),
    textLayer: boolean().notNull().default(false),
  },
  (t) => [
    primaryKey({ columns: [t.uploadId, t.pageNumber] }),
    check("upload_page_number_check", sql`${t.pageNumber} > 0`),
  ],
);
