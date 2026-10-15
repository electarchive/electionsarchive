alter table user_role add constraint user_role_polity_scope_id_polity_id_fkey foreign key (polity_scope_id) references polity (id) on delete cascade;--> statement-breakpoint
alter table changeset add constraint changeset_author_user_id_users_id_fkey foreign key (author_user_id) references users (id) on delete set null;--> statement-breakpoint
alter table changeset add constraint changeset_reviewed_by_users_id_fkey foreign key (reviewed_by) references users (id) on delete set null;
