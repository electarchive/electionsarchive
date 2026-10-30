set local lock_timeout = '10s';
--> statement-breakpoint
alter table coverage_goal rename column lost to known_gap;
--> statement-breakpoint
alter table coverage_goal add column blocked integer not null default 0, add column inapplicable integer not null default 0;
