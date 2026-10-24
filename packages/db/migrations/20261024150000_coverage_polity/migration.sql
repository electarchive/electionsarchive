alter table coverage_goal add column polity text not null default 'us';
--> statement-breakpoint
alter table coverage_goal drop constraint coverage_goal_pkey;
--> statement-breakpoint
alter table coverage_goal add constraint coverage_goal_pkey primary key (polity, goal, state, level, year);
--> statement-breakpoint
create table coverage_polity (
  polity text primary key,
  first integer,
  last integer,
  elections integer not null,
  contests integer not null,
  finest text,
  finest_noun text,
  regions jsonb not null default '{}',
  levels jsonb not null default '[]',
  notes jsonb not null default '[]',
  sources jsonb not null default '{}'
);
