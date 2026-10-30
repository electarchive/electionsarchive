CREATE TYPE "activity_kind" AS ENUM('proposal_submitted', 'proposal_revised', 'changes_requested', 'proposal_rejected', 'proposal_withdrawn', 'proposal_published', 'proposal_reverted', 'changeset_published');--> statement-breakpoint
CREATE TYPE "proposal_job_kind" AS ENUM('check', 'submit', 'publish', 'reject', 'revert');--> statement-breakpoint
CREATE TYPE "proposal_job_status" AS ENUM('queued', 'running', 'done', 'failed');--> statement-breakpoint
CREATE TYPE "proposal_state" AS ENUM('draft', 'open', 'changes_requested', 'approved', 'published', 'rejected', 'withdrawn', 'reverted');--> statement-breakpoint
CREATE TABLE "activity" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7(),
	"at" timestamp(3) with time zone DEFAULT now() NOT NULL,
	"actor_user_id" uuid,
	"agent_key_id" uuid,
	"kind" "activity_kind" NOT NULL,
	"proposal_id" uuid,
	"changeset_id" uuid,
	"entity_ids" uuid[] DEFAULT '{}'::uuid[] NOT NULL,
	"polity_ids" uuid[] DEFAULT '{}'::uuid[] NOT NULL,
	"summary" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "profile" (
	"user_id" uuid PRIMARY KEY,
	"handle" text NOT NULL UNIQUE,
	"display_name" text,
	"bio_md" text,
	"affiliation" text,
	"links" jsonb DEFAULT '[]' NOT NULL,
	"license_accepted_at" timestamp(3) with time zone,
	"feed_token" text UNIQUE,
	"created_at" timestamp(3) with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "proposal" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7(),
	"number" integer UNIQUE GENERATED ALWAYS AS IDENTITY (sequence name "proposal_number_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1000 CACHE 1),
	"author_user_id" uuid,
	"agent_key_id" uuid,
	"agent_name" text,
	"agent_model" text,
	"title" text NOT NULL,
	"polity_id" uuid,
	"state" "proposal_state" DEFAULT 'draft'::"proposal_state" NOT NULL,
	"tier" smallint,
	"head_changeset_id" uuid,
	"draft" jsonb,
	"prescreen" jsonb,
	"prereview" jsonb,
	"decided_by" uuid,
	"decided_at" timestamp(3) with time zone,
	"decision_reason" text,
	"created_at" timestamp(3) with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp(3) with time zone DEFAULT now() NOT NULL,
	"submitted_at" timestamp(3) with time zone,
	CONSTRAINT "proposal_tier_check" CHECK ("tier" between 1 and 3)
);
--> statement-breakpoint
CREATE TABLE "proposal_job" (
	"id" uuid PRIMARY KEY DEFAULT uuidv7(),
	"proposal_id" uuid NOT NULL,
	"revision_n" integer,
	"kind" "proposal_job_kind" NOT NULL,
	"status" "proposal_job_status" DEFAULT 'queued'::"proposal_job_status" NOT NULL,
	"requested_by" uuid,
	"created_at" timestamp(3) with time zone DEFAULT now() NOT NULL,
	"started_at" timestamp(3) with time zone,
	"finished_at" timestamp(3) with time zone,
	"output" jsonb
);
--> statement-breakpoint
CREATE TABLE "proposal_revision" (
	"proposal_id" uuid,
	"n" integer,
	"changeset_id" uuid,
	"payload" jsonb NOT NULL,
	"diff" jsonb,
	"checks" jsonb,
	"note" text,
	"created_at" timestamp(3) with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "proposal_revision_pkey" PRIMARY KEY("proposal_id","n")
);
--> statement-breakpoint
CREATE INDEX "activity_at_idx" ON "activity" ("at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "activity_actor_user_id_at_idx" ON "activity" ("actor_user_id","at" DESC NULLS LAST);--> statement-breakpoint
CREATE INDEX "activity_proposal_id_idx" ON "activity" ("proposal_id");--> statement-breakpoint
CREATE INDEX "activity_entity_ids_idx" ON "activity" USING gin ("entity_ids");--> statement-breakpoint
CREATE INDEX "activity_polity_ids_idx" ON "activity" USING gin ("polity_ids");--> statement-breakpoint
CREATE INDEX "proposal_author_user_id_idx" ON "proposal" ("author_user_id");--> statement-breakpoint
CREATE INDEX "proposal_state_tier_idx" ON "proposal" ("state","tier");--> statement-breakpoint
CREATE INDEX "proposal_polity_id_idx" ON "proposal" ("polity_id");--> statement-breakpoint
CREATE INDEX "proposal_job_queued_idx" ON "proposal_job" ("created_at") WHERE "status" = 'queued';--> statement-breakpoint
CREATE INDEX "proposal_job_proposal_id_idx" ON "proposal_job" ("proposal_id");--> statement-breakpoint
CREATE UNIQUE INDEX "proposal_revision_changeset_id_key" ON "proposal_revision" ("changeset_id");--> statement-breakpoint
ALTER TABLE "activity" ADD CONSTRAINT "activity_actor_user_id_users_id_fkey" FOREIGN KEY ("actor_user_id") REFERENCES "users"("id") ON DELETE SET NULL;--> statement-breakpoint
ALTER TABLE "activity" ADD CONSTRAINT "activity_agent_key_id_apikeys_id_fkey" FOREIGN KEY ("agent_key_id") REFERENCES "apikeys"("id") ON DELETE SET NULL;--> statement-breakpoint
ALTER TABLE "activity" ADD CONSTRAINT "activity_proposal_id_proposal_id_fkey" FOREIGN KEY ("proposal_id") REFERENCES "proposal"("id") ON DELETE CASCADE;--> statement-breakpoint
ALTER TABLE "profile" ADD CONSTRAINT "profile_user_id_users_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE;--> statement-breakpoint
ALTER TABLE "proposal" ADD CONSTRAINT "proposal_author_user_id_users_id_fkey" FOREIGN KEY ("author_user_id") REFERENCES "users"("id") ON DELETE SET NULL;--> statement-breakpoint
ALTER TABLE "proposal" ADD CONSTRAINT "proposal_agent_key_id_apikeys_id_fkey" FOREIGN KEY ("agent_key_id") REFERENCES "apikeys"("id") ON DELETE SET NULL;--> statement-breakpoint
ALTER TABLE "proposal" ADD CONSTRAINT "proposal_decided_by_users_id_fkey" FOREIGN KEY ("decided_by") REFERENCES "users"("id") ON DELETE SET NULL;--> statement-breakpoint
ALTER TABLE "proposal_job" ADD CONSTRAINT "proposal_job_proposal_id_proposal_id_fkey" FOREIGN KEY ("proposal_id") REFERENCES "proposal"("id") ON DELETE CASCADE;--> statement-breakpoint
ALTER TABLE "proposal_job" ADD CONSTRAINT "proposal_job_requested_by_users_id_fkey" FOREIGN KEY ("requested_by") REFERENCES "users"("id") ON DELETE SET NULL;--> statement-breakpoint
ALTER TABLE "proposal_revision" ADD CONSTRAINT "proposal_revision_proposal_id_proposal_id_fkey" FOREIGN KEY ("proposal_id") REFERENCES "proposal"("id") ON DELETE CASCADE;