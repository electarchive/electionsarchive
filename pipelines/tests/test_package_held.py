"""`ea package ready` finds external ids, slugs, and entity ids that the load would refuse.

The batch checks read only the built packages. The held checks read the reference partitions, so the test writes a
small partition folder beside a snapshot and passes a `Reference` that points at it.
"""

import json
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

import polars as pl

from ea import package
from ea.package import Reference, _held, _repeated

A, B, C = (f"00000000-0000-0000-0000-00000000000{n}" for n in (1, 2, 3))


class Held(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        patch = mock.patch.object(package, "ROOT", self.tmp / "packages")
        patch.start()
        self.addCleanup(patch.stop)

    def build(self, key: str, pipeline: str = "xx.test", **tables: Sequence[Mapping[str, str | bool | None]]) -> Path:
        path = self.tmp / "packages" / key / "build"
        path.mkdir(parents=True)
        (path / "package.json").write_text(
            json.dumps({"key": key, "pipeline": pipeline, "depends": [], "external": []})
        )
        for table, rows in tables.items():
            pl.DataFrame(rows).write_parquet(path / f"{table}.parquet")
        return path

    def reference(self, **tables: Sequence[Mapping[str, str | bool | None]]) -> Reference:
        folder = self.tmp / "reference" / "xx"
        folder.mkdir(parents=True)
        for table, rows in tables.items():
            pl.DataFrame(rows).write_parquet(folder / f"{table}.parquet")
        return Reference(frozenset(), frozenset(), {}, self.tmp / "reference")

    def clashes(self, supersedes: str | None) -> list[str]:
        row: dict[str, str | bool | None] = {
            "id": A, "election_id": B, "contest_id": C, "stage": "preliminary", "is_default": True,
            "supersedes_id": supersedes,
        }  # fmt: skip
        path = self.build("xx.test/a", result_set=[row])
        conn = mock.MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            (B, C, "preliminary", "polling_station", "xx.other", A[:-1] + "9")
        ]
        with mock.patch.object(package, "_grains", return_value={A: "polling_station"}):
            return package._clashes(conn, path, "xx.test")

    def test_default_naming_the_set_it_replaces_is_not_a_clash(self) -> None:
        self.assertEqual(self.clashes(A[:-1] + "9"), [])

    def test_default_naming_another_set_is_still_a_clash(self) -> None:
        found = self.clashes(A[:-1] + "8")
        self.assertEqual([f.split(":")[0] for f in found], ["ready.default"])

    def test_default_naming_nothing_is_still_a_clash(self) -> None:
        self.assertEqual(len(self.clashes(None)), 1)

    def test_external_id_held_by_another_pipeline(self) -> None:
        path = self.build("xx.test/a", external_id=[{"entity_id": A, "scheme": "s", "value": "v"}])
        held = [{"entity_id": B, "scheme": "s", "value": "v", "pipeline": "xx.registry"}]
        found = _held(self.reference(external_id=held), path, "xx.test")
        self.assertEqual([f.split(":")[0] for f in found], ["ready.external_id"])

    def test_external_id_held_by_the_same_pipeline_is_a_re_emit(self) -> None:
        path = self.build("xx.test/a", external_id=[{"entity_id": A, "scheme": "s", "value": "v"}])
        held = [{"entity_id": A, "scheme": "s", "value": "v", "pipeline": "xx.test"}]
        self.assertEqual(_held(self.reference(external_id=held), path, "xx.test"), [])

    def test_external_id_added_by_two_packages(self) -> None:
        row = [{"entity_id": A, "scheme": "s", "value": "v"}]
        self.build("xx.test/a", external_id=row)
        self.build("xx.test/b", external_id=row)
        found = _repeated(["xx.test/a", "xx.test/b"])
        self.assertTrue(any(f.startswith("ready.external_id") for f in found), found)

    def test_second_current_slug_from_the_reference(self) -> None:
        path = self.build("xx.test/a", slug=[{"path": "new", "entity_id": A, "is_current": True}])
        held = [{"path": "old", "entity_id": A, "is_current": True, "pipeline": "xx.other"}]
        found = _held(self.reference(slug=held), path, "xx.test")
        self.assertTrue(any("second one" in f for f in found), found)

    def test_second_current_slug_from_another_package(self) -> None:
        self.build("xx.test/a", slug=[{"path": "one", "entity_id": A, "is_current": True}])
        self.build("xx.test/b", slug=[{"path": "two", "entity_id": A, "is_current": True}])
        found = _repeated(["xx.test/a", "xx.test/b"])
        self.assertTrue(any("current slugs" in f for f in found), found)

    def test_slug_path_held_for_another_entity(self) -> None:
        path = self.build("xx.test/a", slug=[{"path": "p", "entity_id": A, "is_current": True}])
        held = [{"path": "p", "entity_id": B, "is_current": True, "pipeline": "xx.other"}]
        found = _held(self.reference(slug=held), path, "xx.test")
        self.assertTrue(any("another entity" in f for f in found), found)

    def test_slug_path_for_two_entities_in_the_run(self) -> None:
        self.build("xx.test/a", slug=[{"path": "p", "entity_id": A, "is_current": True}])
        self.build("xx.test/b", slug=[{"path": "p", "entity_id": B, "is_current": True}])
        self.assertTrue(any("2 entities" in f for f in _repeated(["xx.test/a", "xx.test/b"])))

    def test_entity_added_by_two_packages(self) -> None:
        self.build("xx.test/a", contest=[{"id": C}])
        self.build("xx.test/b", contest=[{"id": C}])
        found = _repeated(["xx.test/a", "xx.test/b"])
        self.assertEqual([f.split(":")[0] for f in found], ["ready.entity_twice"])

    def test_clean_run(self) -> None:
        self.build("xx.test/a", contest=[{"id": A}], slug=[{"path": "a", "entity_id": A, "is_current": True}])
        self.build("xx.test/b", contest=[{"id": B}], slug=[{"path": "b", "entity_id": B, "is_current": True}])
        self.assertEqual(_repeated(["xx.test/a", "xx.test/b"]), [])


if __name__ == "__main__":
    unittest.main()
