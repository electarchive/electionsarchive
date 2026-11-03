create table mcp_usage (
  day date not null,
  caller text not null check (length(caller) between 1 and 80),
  client text not null check (length(client) between 1 and 80),
  tool text not null check (length(tool) between 1 and 80),
  key_id uuid references apikeys (id) on delete set null,
  user_id uuid references users (id) on delete cascade,
  calls integer not null check (calls > 0),
  last_at timestamptz not null,
  primary key (day, caller, client, tool)
);--> statement-breakpoint
create index mcp_usage_key_id_idx on mcp_usage (key_id, day) where key_id is not null;--> statement-breakpoint
create index mcp_usage_user_id_idx on mcp_usage (user_id, day) where user_id is not null;
