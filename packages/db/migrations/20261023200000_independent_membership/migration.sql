alter table person_party_membership drop constraint person_party_membership_pkey;
alter table person_party_membership alter column party_id drop not null;
alter table person_party_membership
  add constraint person_party_membership_key unique nulls not distinct (person_id, party_id, valid without overlaps);
alter table person_party_membership_history alter column party_id drop not null;
