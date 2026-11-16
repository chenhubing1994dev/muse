from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from muse.memory import MemoryStore
from muse.memory_cli import main
from muse.obsidian_vault import export_approved_memories, export_research_wiki
from muse.research_knowledge import ResearchKnowledgeStore


class ObsidianVaultExportTests(unittest.TestCase):
    def test_exports_approved_memories_as_readable_markdown_with_obsidian_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with MemoryStore(root / "memory.sqlite3") as memory:
                profile = memory.add("User prefers natural Cantonese", project="profile", source="user", confidence=1.0)
                project = memory.add("Muse uses SQLite for task state", project="Muse", source="decision")
                report = export_approved_memories(memory, root / "Vault")

            self.assertEqual(report["created"], 2)
            self.assertEqual(report["skipped_existing"], 0)
            profile_note = root / "Vault" / "01-Profile" / f"memory-{profile['id']:06d}.md"
            project_note = root / "Vault" / "02-Projects" / "muse" / f"memory-{project['id']:06d}.md"
            self.assertTrue(profile_note.is_file())
            self.assertTrue(project_note.is_file())
            profile_text = profile_note.read_text(encoding="utf-8")
            self.assertIn("muse_id: 1", profile_text)
            self.assertIn("User prefers natural Cantonese", profile_text)
            self.assertIn('source: "user"', profile_text)
            self.assertIn("kind: fact", profile_text)
            self.assertIn("track: user", profile_text)
            index = (root / "Vault" / "Muse Memory Index.md").read_text(encoding="utf-8")
            self.assertTrue(index.startswith("# Muse Memory Index"))
            self.assertIn("[[01-Profile/memory-000001]]", index)
            self.assertIn("[[02-Projects/muse/memory-000002]]", index)

    def test_existing_notes_are_never_overwritten_and_conflicts_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with MemoryStore(root / "memory.sqlite3") as memory:
                saved = memory.add("Original memory", project="Muse", source="test")
                vault = root / "Vault"
                note = vault / "02-Projects" / "muse" / f"memory-{saved['id']:06d}.md"
                note.parent.mkdir(parents=True)
                note.write_text("My manual Obsidian edit", encoding="utf-8")
                index = vault / "Muse Memory Index.md"
                index.write_text("My manual index entry\n", encoding="utf-8")

                report = export_approved_memories(memory, vault)

            self.assertEqual(report["created"], 0)
            self.assertEqual(report["skipped_existing"], 1)
            self.assertEqual(note.read_text(encoding="utf-8"), "My manual Obsidian edit")
            index_text = index.read_text(encoding="utf-8")
            self.assertTrue(index_text.startswith("# Muse Memory Index"))
            self.assertIn("My manual index entry", index_text)

    def test_deleted_memories_are_not_exported_or_removed_from_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with MemoryStore(root / "memory.sqlite3") as memory:
                saved = memory.add("Archived memory", project="Muse", source="test")
                memory.delete(saved["id"])
                report = export_approved_memories(memory, root / "Vault")
            self.assertEqual(report["created"], 0)
            self.assertEqual(list((root / "Vault").rglob("memory-*.md")), [])

    def test_rejects_vault_path_inside_memory_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "memory.sqlite3"
            with MemoryStore(database) as memory:
                with self.assertRaises(ValueError):
                    export_approved_memories(memory, database)


    def test_memory_cli_exports_vault_and_reports_created_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "memory.sqlite3"
            with MemoryStore(database) as memory:
                memory.add("CLI-exported memory", project="Muse", source="test")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result_code = main(["--db", str(database), "export-obsidian", "--vault", str(root / "Vault")])
            self.assertEqual(result_code, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["created"], 1)
            self.assertEqual(report["sync_direction"], "sqlite_to_obsidian")
            self.assertTrue((root / "Vault" / "02-Projects" / "muse" / "memory-000001.md").is_file())


class ResearchWikiExportTests(unittest.TestCase):
    def test_exports_only_explicit_research_nodes_and_selected_graph_edges_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "Vault"
            vault.mkdir()
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                source = knowledge.upsert_node(
                    "source", "Public source", summary="A readable source summary.",
                    external_ref="https://example.org/paper", provenance=("https://example.org/paper",),
                )
                claim = knowledge.upsert_node(
                    "claim", "Proposed hypothesis", summary="This remains a hypothesis.",
                    metadata={"epistemic_status": "hypothesis", "private_feedback": "must not export"},
                    status="proposed",
                )
                unrelated = knowledge.upsert_node("idea", "Not selected", summary="Do not export.")
                knowledge.add_edge(source["id"], claim["id"], "supports")
                report = export_research_wiki(
                    knowledge, vault, node_ids=[source["id"], claim["id"]],
                )
                self.assertEqual(report["selected"], 2)
                self.assertEqual(report["created"], 2)
                self.assertEqual(report["edge_count"], 1)
                self.assertEqual(report["source_bodies_exported"], 0)
                source_note = vault / "05-Research-Wiki" / "source" / f"{source['id']}.md"
                claim_note = vault / "05-Research-Wiki" / "claim" / f"{claim['id']}.md"
                source_text = source_note.read_text(encoding="utf-8")
                claim_text = claim_note.read_text(encoding="utf-8")
                self.assertIn("Public source", source_text)
                self.assertIn("[[05-Research-Wiki/claim/" + claim["id"] + "|Proposed hypothesis]]", source_text)
                self.assertIn("proposed", claim_text)
                self.assertNotIn("must not export", claim_text)
                self.assertNotIn(unrelated["id"], "".join(str(item) for item in report["created_paths"]))
                self.assertTrue((vault / "Muse Research Wiki Index.md").is_file())
                source_note.write_text("manual edit", encoding="utf-8")
                rerun = export_research_wiki(knowledge, vault, node_ids=[source["id"], claim["id"]])
                self.assertEqual(rerun["created"], 0)
                self.assertEqual(rerun["skipped_existing"], 2)
                self.assertEqual(source_note.read_text(encoding="utf-8"), "manual edit")
                self.assertFalse(rerun["index_created"])

    def test_research_wiki_export_requires_explicit_active_ids_and_existing_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                node = knowledge.upsert_node("idea", "Candidate")
                with self.assertRaises(FileNotFoundError):
                    export_research_wiki(knowledge, root / "missing", node_ids=[node["id"]])
                with self.assertRaises(ValueError):
                    export_research_wiki(knowledge, root, node_ids=["0" * 32])
                with self.assertRaises(ValueError):
                    export_research_wiki(knowledge, root, node_ids=[])

    def test_research_wiki_cli_requires_explicit_node_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "knowledge.sqlite3"
            with ResearchKnowledgeStore(database) as knowledge:
                node = knowledge.upsert_node("idea", "CLI research node")
            vault = root / "Vault"
            vault.mkdir()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result_code = main([
                    "export-research-wiki", "--knowledge-db", str(database),
                    "--vault", str(vault), "--node-id", node["id"],
                ])
            self.assertEqual(result_code, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["created"], 1)
            self.assertTrue(report["explicit_selection_required"])


class ObsidianVaultAuditTests(unittest.TestCase):
    def _setup(self, root: Path):
        memory = MemoryStore(root / "memory.sqlite3")
        saved = memory.add("Canonical memory", project="Muse", source="test")
        vault = root / "Vault"
        export_approved_memories(memory, vault)
        note = vault / "02-Projects" / "muse" / f"memory-{saved['id']:06d}.md"
        return memory, saved, vault, note

    def test_audit_reports_unchanged_managed_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory, saved, vault, note = self._setup(root)
            try:
                from muse.obsidian_vault import audit_vault
                report = audit_vault(memory, vault)
                self.assertEqual(report["unchanged"], 1)
                self.assertEqual(report["modified"], 0)
                self.assertEqual(report["missing"], 0)
                self.assertEqual(report["items"][0]["memory_id"], saved["id"])
                self.assertEqual(report["items"][0]["status"], "unchanged")
                self.assertTrue(report["items"][0]["sha256"])
            finally:
                memory.close()

    def test_audit_recognizes_pre_classification_notes_as_legacy_not_external_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory, _saved, vault, note = self._setup(root)
            try:
                text = note.read_text(encoding="utf-8")
                text = text.replace("kind: fact\n", "").replace("track: user\n", "")
                note.write_text(text, encoding="utf-8")
                from muse.obsidian_vault import audit_vault
                report = audit_vault(memory, vault)
                self.assertEqual(report["legacy_metadata"], 1)
                self.assertEqual(report["conflicts"], 0)
                self.assertEqual(report["items"][0]["status"], "legacy_metadata")
            finally:
                memory.close()

    def test_audit_detects_manual_edit_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory, _saved, vault, note = self._setup(root)
            try:
                original = note.read_text(encoding="utf-8")
                note.write_text(original.replace("Canonical memory", "Manual edit"), encoding="utf-8")
                from muse.obsidian_vault import audit_vault
                report = audit_vault(memory, vault)
                self.assertEqual(report["modified"], 1)
                self.assertEqual(report["items"][0]["status"], "external_edit_detected")
                self.assertIn("Manual edit", note.read_text(encoding="utf-8"))
            finally:
                memory.close()

    def test_audit_reports_missing_managed_note_and_ignores_unmanaged_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory, _saved, vault, note = self._setup(root)
            try:
                note.unlink()
                manual = vault / "My own note.md"
                manual.write_text("A private note without Muse metadata", encoding="utf-8")
                from muse.obsidian_vault import audit_vault
                report = audit_vault(memory, vault)
                self.assertEqual(report["missing"], 1)
                self.assertEqual(report["items"][0]["status"], "missing")
                self.assertEqual(report["unmanaged_notes_ignored"], 1)
                self.assertTrue(manual.exists())
            finally:
                memory.close()

    def test_audit_marks_malformed_managed_frontmatter_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory, _saved, vault, note = self._setup(root)
            try:
                note.write_text("This file no longer has Muse frontmatter", encoding="utf-8")
                from muse.obsidian_vault import audit_vault
                report = audit_vault(memory, vault)
                self.assertEqual(report["conflicts"], 1)
                self.assertEqual(report["items"][0]["status"], "malformed_managed_note")
            finally:
                memory.close()

    def test_audit_distinguishes_stale_sqlite_revision_and_orphaned_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory, saved, vault, note = self._setup(root)
            try:
                memory.revise(saved["id"], "New canonical memory")
                orphan = vault / "Old" / "memory-999999.md"
                orphan.parent.mkdir()
                orphan.write_text("---\nmuse_id: 999999\nrevision: 1\n---\nOld", encoding="utf-8")
                from muse.obsidian_vault import audit_vault
                report = audit_vault(memory, vault)
                statuses = {item["status"] for item in report["items"]}
                self.assertIn("stale_source_revision", statuses)
                self.assertIn("orphaned_managed_note", statuses)
                self.assertTrue(note.exists())
            finally:
                memory.close()

    def test_audit_cli_emits_json_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "memory.sqlite3"
            with MemoryStore(database) as memory:
                memory.add("CLI audit", project="Muse", source="test")
                vault = root / "Vault"
                export_approved_memories(memory, vault)
            before = sorted((p.relative_to(vault).as_posix(), p.read_bytes()) for p in vault.rglob("*" ) if p.is_file())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--db", str(database), "audit-obsidian", "--vault", str(vault)])
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["unchanged"], 1)
            after = sorted((p.relative_to(vault).as_posix(), p.read_bytes()) for p in vault.rglob("*") if p.is_file())
            self.assertEqual(before, after)

    def test_audit_requires_existing_vault_and_never_creates_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with MemoryStore(root / "memory.sqlite3") as memory:
                missing_vault = root / "not-created"
                from muse.obsidian_vault import audit_vault
                with self.assertRaises(FileNotFoundError):
                    audit_vault(memory, missing_vault)
                self.assertFalse(missing_vault.exists())


class ResearchWikiAuditTests(unittest.TestCase):
    def _export(self, root: Path):
        vault = root / "Vault"
        vault.mkdir()
        knowledge = ResearchKnowledgeStore(root / "knowledge.sqlite3")
        node = knowledge.upsert_node(
            "source", "Auditable source", summary="Original summary.",
            external_ref="https://example.org/auditable",
        )
        export_research_wiki(knowledge, vault, node_ids=[node["id"]])
        note = vault / "05-Research-Wiki" / "source" / f"{node['id']}.md"
        return knowledge, node, vault, note

    def test_audit_reports_clean_hash_and_cli_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge, node, vault, note = self._export(root)
            try:
                from muse.obsidian_vault import audit_research_wiki
                before = sorted((p.relative_to(vault).as_posix(), p.read_bytes())
                                for p in vault.rglob("*") if p.is_file())
                report = audit_research_wiki(knowledge, vault)
                self.assertEqual(report["clean"], 1)
                self.assertEqual(report["items"][0]["node_id"], node["id"])
                self.assertEqual(len(report["items"][0]["body_sha256"]), 64)
                self.assertEqual((report["writes"], report["deletes"], report["automatic_repairs"]), (0, 0, 0))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(["audit-research-wiki", "--knowledge-db", str(root / "knowledge.sqlite3"),
                                 "--vault", str(vault)])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output.getvalue())["clean"], 1)
                after = sorted((p.relative_to(vault).as_posix(), p.read_bytes())
                               for p in vault.rglob("*") if p.is_file())
                self.assertEqual(before, after)
            finally:
                knowledge.close()

    def test_audit_detects_external_body_edit_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge, _node, vault, note = self._export(root)
            try:
                original = note.read_text(encoding="utf-8")
                note.write_text(original.replace("Original summary.", "Edited summary."), encoding="utf-8")
                from muse.obsidian_vault import audit_research_wiki
                report = audit_research_wiki(knowledge, vault)
                self.assertEqual(report["external_edit"], 1)
                self.assertIn("Edited summary.", note.read_text(encoding="utf-8"))
            finally:
                knowledge.close()

    def test_audit_detects_stale_source_and_orphaned_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge, node, vault, note = self._export(root)
            try:
                knowledge.upsert_node(
                    "source", "Auditable source", summary="Updated summary.",
                    external_ref="https://example.org/auditable",
                )
                from muse.obsidian_vault import audit_research_wiki
                report = audit_research_wiki(knowledge, vault)
                self.assertEqual(report["stale_source"], 1)
                knowledge.tombstone_node(node["id"], actor="test", reason="orphan audit")
                report = audit_research_wiki(knowledge, vault)
                self.assertEqual(report["orphaned"], 1)
                self.assertTrue(note.exists())
            finally:
                knowledge.close()

    def test_audit_marks_old_export_without_hash_as_legacy_unverifiable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge, _node, vault, note = self._export(root)
            try:
                text = note.read_text(encoding="utf-8")
                text = "\n".join(line for line in text.splitlines()
                                 if not line.startswith("body_sha256:"))
                note.write_text(text, encoding="utf-8")
                from muse.obsidian_vault import audit_research_wiki
                report = audit_research_wiki(knowledge, vault)
                self.assertEqual(report["legacy_unverifiable"], 1)
            finally:
                knowledge.close()

    def test_audit_ignores_unmanaged_notes_and_reports_malformed_managed_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge, node, vault, note = self._export(root)
            try:
                unmanaged = vault / "05-Research-Wiki" / "my-own-note.md"
                unmanaged.write_text("# Personal note", encoding="utf-8")
                wrong = vault / "05-Research-Wiki" / "idea" / f"{node['id']}.md"
                wrong.parent.mkdir(parents=True)
                wrong.write_text(note.read_text(encoding="utf-8"), encoding="utf-8")
                note.unlink()
                from muse.obsidian_vault import audit_research_wiki
                report = audit_research_wiki(knowledge, vault)
                self.assertEqual(report["malformed"], 1)
                self.assertEqual(report["unmanaged_notes_ignored"], 1)
                self.assertTrue(unmanaged.exists())
            finally:
                knowledge.close()


if __name__ == "__main__":
    unittest.main()
