create type upload_status as enum ('received', 'ready', 'rejected');--> statement-breakpoint
create table upload (
  id uuid primary key default uuidv7(),
  owner_user_id uuid references users (id) on delete set null,
  agent_key_id uuid references apikeys (id) on delete set null,
  sha256 text not null,
  media_type text not null,
  bytes bigint not null,
  filename text not null,
  storage_key text,
  pages integer,
  status upload_status not null default 'received',
  error text,
  public_record boolean not null default false,
  created_at timestamp(3) with time zone not null default now(),
  constraint upload_sha256_check check (sha256 ~ '^[0-9a-f]{64}$'),
  constraint upload_bytes_check check (bytes > 0 and bytes <= 209715200),
  constraint upload_ready_check check (status <> 'ready' or storage_key is not null)
);--> statement-breakpoint
create unique index upload_owner_sha256_idx on upload (owner_user_id, sha256);--> statement-breakpoint
create index upload_sha256_idx on upload (sha256);--> statement-breakpoint
create table upload_page (
  upload_id uuid not null references upload (id) on delete cascade,
  page_number integer not null,
  image_key text not null,
  width integer not null,
  height integer not null,
  text_layer boolean not null default false,
  primary key (upload_id, page_number),
  constraint upload_page_number_check check (page_number > 0)
);--> statement-breakpoint
create function upload_notify() returns trigger language plpgsql as $$
begin
  perform pg_notify('proposal_job', json_build_object('kind', 'upload', 'id', new.id)::text);
  return null;
end
$$;--> statement-breakpoint
create trigger upload_notify after insert on upload for each row execute function upload_notify();
