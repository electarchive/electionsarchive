create index source_icpsr_idx on source (id) where license ilike '%ICPSR%';--> statement-breakpoint
create index body_polity_idx on body (polity_id);--> statement-breakpoint
create index office_polity_idx on office (polity_id);--> statement-breakpoint
create index office_body_idx on office (body_id);--> statement-breakpoint
create index tenure_body_idx on tenure (body_id);--> statement-breakpoint
create index polity_parent_parent_idx on polity_parent (parent_id);
