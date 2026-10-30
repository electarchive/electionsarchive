set lock_timeout = '10s';--> statement-breakpoint
alter table proposal add constraint proposal_polity_id_polity_id_fkey foreign key (polity_id) references polity (id) on delete set null;--> statement-breakpoint
alter table proposal add constraint proposal_head_changeset_id_changeset_id_fkey foreign key (head_changeset_id) references changeset (id) on delete set null;--> statement-breakpoint
alter table proposal_revision add constraint proposal_revision_changeset_id_changeset_id_fkey foreign key (changeset_id) references changeset (id) on delete set null;--> statement-breakpoint
alter table activity add constraint activity_changeset_id_changeset_id_fkey foreign key (changeset_id) references changeset (id) on delete set null;--> statement-breakpoint
create function proposal_job_notify() returns trigger language plpgsql as $$
begin
  perform pg_notify('proposal_job', json_build_object('id', new.id, 'proposal_id', new.proposal_id, 'kind', new.kind, 'status', new.status)::text);
  return null;
end
$$;--> statement-breakpoint
create trigger proposal_job_notify after insert or update of status on proposal_job for each row execute function proposal_job_notify();
