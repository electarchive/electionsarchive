create type archive_capture_status as enum ('captured', 'blocked', 'failed');--> statement-breakpoint
create table archive_capture (
  id uuid primary key default uuidv7(),
  owner_user_id uuid references users (id) on delete set null,
  url text not null,
  final_url text,
  wayback_url text,
  status archive_capture_status not null,
  error text,
  raw_sha256 text,
  raw_key text,
  media_type text,
  bytes bigint,
  captured_at timestamp(3) with time zone not null default now(),
  constraint archive_capture_sha256_check check (raw_sha256 ~ '^[0-9a-f]{64}$'),
  constraint archive_capture_raw_check check ((status = 'captured') = (raw_sha256 is not null and raw_key is not null))
);--> statement-breakpoint
create index archive_capture_owner_idx on archive_capture (owner_user_id, captured_at desc);--> statement-breakpoint
create index archive_capture_sha256_idx on archive_capture (raw_sha256);
