"""Scratch-database test: a unit moving from an older package of the run to a new one with a wider validity."""

import os
import shutil
import subprocess
import unittest

import psycopg

from ea.package import _vacate_units
from tests.test_package_supersede import BIN, _cluster

OLD_CS, NEW_CS, OTHER_CS = (f"00000000-0000-0000-0000-0000000000c{n}" for n in (1, 2, 3))
OLD_UNIT, NEW_UNIT = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


class Vacate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn, cls.root = _cluster()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.root and BIN:
            run = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
            subprocess.run(
                [*run, str(BIN / "pg_ctl"), "-D", f"{cls.root}/data", "-m", "immediate", "stop"], check=False
            )
            shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        self.conn = psycopg.connect(self.dsn)
        self.conn.execute("create extension if not exists btree_gist")
        self.conn.execute("drop table if exists reporting_unit, pkg_reporting_unit, changeset")
        self.conn.execute("create table changeset (id uuid, pipeline text, scope jsonb)")
        self.conn.execute(
            """create table reporting_unit (id uuid primary key, scheme_code text, code text, changeset_id uuid,
               valid_from date, valid_to date,
               valid daterange generated always as (daterange(valid_from, valid_to, '[)')) stored,
               exclude using gist (scheme_code with =, code with =, valid with &&))"""
        )
        self.conn.execute(
            "create table pkg_reporting_unit (id uuid, scheme_code text, code text, valid_from date, valid_to date)"
        )
        for cs, package, pipeline in ((OLD_CS, "ro/1957", "ro"), (NEW_CS, "ro/geo", "ro"), (OTHER_CS, "xx/a", "xx")):
            self.conn.execute(
                "insert into changeset values (%s, %s, jsonb_build_object('package', %s::text))",
                [cs, pipeline, package],
            )
        self.conn.execute(
            "insert into pkg_reporting_unit values (%s, 'ro.regiune', 'b', '1957-02-03', '1961-03-05')", [NEW_UNIT]
        )

    def tearDown(self) -> None:
        self.conn.close()

    def old(self, changeset: str) -> None:
        self.conn.execute(
            "insert into reporting_unit (id, scheme_code, code, changeset_id, valid_from, valid_to) "
            "values (%s, 'ro.regiune', 'b', %s, '1957-02-03', '1957-02-04')",
            [OLD_UNIT, changeset],
        )

    def insert_new(self) -> None:
        self.conn.execute(
            "insert into reporting_unit (id, scheme_code, code, changeset_id, valid_from, valid_to) "
            "select id, scheme_code, code, %s, valid_from, valid_to from pkg_reporting_unit",
            [NEW_CS],
        )

    def test_older_package_of_the_run_gives_way(self) -> None:
        self.old(OLD_CS)
        with self.assertRaises(psycopg.errors.ExclusionViolation), self.conn.transaction():
            self.insert_new()
        self.assertEqual(_vacate_units(self.conn, "ro/geo", "ro", frozenset({"ro/1957", "ro/geo"})), 1)
        self.insert_new()
        self.assertEqual(self.conn.execute("select count(*) from reporting_unit").fetchone(), (2,))

    def test_package_outside_the_run_is_left_alone(self) -> None:
        self.old(OLD_CS)
        self.assertEqual(_vacate_units(self.conn, "ro/geo", "ro", frozenset({"ro/geo"})), 0)

    def test_other_pipeline_is_left_alone(self) -> None:
        self.old(OTHER_CS)
        self.assertEqual(_vacate_units(self.conn, "ro/geo", "ro", frozenset({"xx/a", "ro/geo"})), 0)


if __name__ == "__main__":
    unittest.main()
