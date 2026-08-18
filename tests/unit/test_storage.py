"""File store and registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from qualagent.domain.errors import ProjectNotFound
from qualagent.storage.files import FileStore, sha256_bytes
from qualagent.storage.registry import ProjectRegistry


class TestFileStore:
    def test_store_and_read(self, tmp_path: Path) -> None:
        store = FileStore(tmp_path)
        data = b"interview text"
        digest = store.store(data)
        assert digest == sha256_bytes(data)
        assert store.exists(digest)
        assert store.read(digest) == data
        assert store.path_for(digest).parent == tmp_path / "files"

    def test_dedup(self, tmp_path: Path) -> None:
        store = FileStore(tmp_path)
        d1 = store.store(b"same")
        d2 = store.store(b"same")
        assert d1 == d2
        assert len(list((tmp_path / "files").iterdir())) == 1

    def test_sha256_known_vector(self) -> None:
        assert sha256_bytes(b"abc") == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


class TestRegistry:
    def test_register_and_list(self, registry_path: Path, tmp_path: Path) -> None:
        reg = ProjectRegistry(registry_path)
        reg.register("id-1", "study-a", tmp_path / "a")
        reg.register("id-2", "study-b", tmp_path / "b")
        names = [e.name for e in reg.list_all()]
        assert names == ["study-a", "study-b"]

    def test_persistence_across_instances(self, registry_path: Path, tmp_path: Path) -> None:
        ProjectRegistry(registry_path).register("id-1", "study-a", tmp_path / "a")
        assert ProjectRegistry(registry_path).get("id-1").name == "study-a"

    def test_get_missing_raises(self, registry_path: Path) -> None:
        with pytest.raises(ProjectNotFound):
            ProjectRegistry(registry_path).get("nope")

    def test_find_by_name(self, registry_path: Path, tmp_path: Path) -> None:
        reg = ProjectRegistry(registry_path)
        reg.register("id-1", "study-a", tmp_path / "a")
        assert reg.find_by_name("study-a") is not None
        assert reg.find_by_name("missing") is None

    def test_unregister(self, registry_path: Path, tmp_path: Path) -> None:
        reg = ProjectRegistry(registry_path)
        reg.register("id-1", "study-a", tmp_path / "a")
        reg.unregister("id-1")
        assert reg.list_all() == []
        with pytest.raises(ProjectNotFound):
            reg.unregister("id-1")
