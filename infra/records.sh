#!/bin/bash
cd "$HOME/electionsarchive/pipelines" || exit 1
late=$(psql electionsarchive -tAc "select count(*) from changeset where status = 'published' and published_at > (select coalesce(min(built_at), '-infinity') from polity_record)")
[ "$late" = 0 ] && { echo "polity records are newer than every publish"; exit 0; }
exec ../data/logs/gate.sh uv run ea records
