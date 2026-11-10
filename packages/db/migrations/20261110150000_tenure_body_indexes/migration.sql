create index if not exists tenure_body_to_date_idx on tenure (body_id, to_date);--> statement-breakpoint
create index if not exists tenure_body_seat_succession_idx on tenure (body_id, seat_id) where acquired_via in ('appointed', 'acting', 'succession', 'list_replacement', 'co_opted', 'trustee');
