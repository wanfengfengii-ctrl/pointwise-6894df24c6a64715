import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from admission.evidence import CaptureEvidence
from archive.artifacts import CaptureNotFound
from archive.collect import CaptureRejected
from archive.feasibility import NoExplanation
from archive.storage import ArchiveError, SqliteArchive
from plans.registry import PlanRegistry
from recordings.events import FrameEvent
from review.export import ExportError, export
from review.query import query
from tests.test_capture import capture_file, write_recording


class ArchiveStorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / 'archive.sqlite3'
        self.recording = self.root / 'station.jsonl'
        self.plan = PlanRegistry().register('station-12', 2, {'x': 1, 'y': 2})

    def test_reinitialize(self):
        declaration = self.root / 'station-plan.json'
        declaration.write_text(json.dumps({'key': 'station-12', 'bit_width': 2, 'loops': {'x': 1, 'y': 2}}), encoding='utf-8')
        initial = PlanRegistry().register(**json.loads(declaration.read_text(encoding='utf-8')))
        source = write_recording(self.recording, (('a', 1, 0), ('b', 1, 0), ('c', 2, 1), ('d', 2, 1)))
        with SqliteArchive.open(self.database) as archive:
            original_id = capture_file(archive, initial, source)
            original_record = export(archive, original_id, 1, 2)

        edited = {'key': 'station-12', 'bit_width': 3, 'loops': {'x': 4}}
        declaration.write_text(json.dumps(edited), encoding='utf-8')
        updated = PlanRegistry().register(**json.loads(declaration.read_text(encoding='utf-8')))
        write_recording(self.recording, (('a', 4, 1), ('b', 4, 1)), width=3)
        edited_recording = source.read_bytes()
        edited_declaration = declaration.read_bytes()
        with SqliteArchive.open(self.database) as reopened:
            with self.assertRaises(CaptureNotFound):
                reopened.get(original_id + 1)
            updated_id = capture_file(reopened, updated, source)
        for _ in range(2):
            with SqliteArchive.open(self.database) as reopened:
                self.assertEqual(export(reopened, original_id, 1, 2), original_record)
                self.assertEqual(reopened.get(original_id).plan, initial)
                self.assertEqual(query(reopened, original_id, 1, 2), [(0, 1)])
                self.assertEqual(reopened.get(updated_id).plan, updated)
                self.assertEqual(query(reopened, updated_id, 4), [(1,)])
                with self.assertRaises(CaptureNotFound):
                    reopened.get(updated_id + 1)
        self.assertEqual(source.read_bytes(), edited_recording)
        self.assertEqual(declaration.read_bytes(), edited_declaration)

    def test_incompatible_schema_is_preserved(self):
        with closing(sqlite3.connect(self.database)) as other:
            other.execute('CREATE TABLE operator_notes (note TEXT)')
            other.execute('INSERT INTO operator_notes VALUES (?)', ('巡检留档',))
            other.commit()
        with self.assertRaises(ArchiveError):
            SqliteArchive.open(self.database)
        with closing(sqlite3.connect(self.database)) as other:
            self.assertEqual(other.execute('PRAGMA user_version').fetchone(), (0,))
            self.assertEqual(other.execute('SELECT note FROM operator_notes').fetchall(), [('巡检留档',)])

    def test_damaged_payload_cannot_be_exported(self):
        source = write_recording(self.recording, (('a', 1, 0),))
        with SqliteArchive.open(self.database) as archive:
            capture_id = capture_file(archive, self.plan, source)
        invalid_payloads = ('{', json.dumps({
            'plan': {'key': 'station-12', 'bit_width': 2, 'loops': [['x', 1]]},
            'frames': [[2, 'a', 1, 0], [3, 'a', 1, 0]],
        }))
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with closing(sqlite3.connect(self.database)) as editor:
                    editor.execute('UPDATE captures SET evidence = ? WHERE capture_id = ?', (payload, capture_id))
                    editor.commit()
                with SqliteArchive.open(self.database) as reopened:
                    with self.assertRaises(ArchiveError):
                        reopened.get(capture_id)
                    with self.assertRaises(ExportError):
                        export(reopened, capture_id, 1)

    def test_sqlite_insert_failure_rolls_back(self):
        source = write_recording(self.recording, (('a', 1, 1), ('b', 1, 1)))
        with SqliteArchive.open(self.database) as archive:
            old_id = capture_file(archive, self.plan, source)
            old_record = export(archive, old_id, 1)
        with closing(sqlite3.connect(self.database)) as editor:
            editor.execute(
                "CREATE TRIGGER reject_insert BEFORE INSERT ON captures "
                "BEGIN SELECT RAISE(ABORT, 'write refused'); END"
            )
            editor.commit()
        with SqliteArchive.open(self.database) as archive:
            with self.assertRaisesRegex(CaptureRejected, 'write refused'):
                capture_file(archive, self.plan, source)
        with SqliteArchive.open(self.database) as reopened:
            self.assertEqual(export(reopened, old_id, 1), old_record)
            with self.assertRaises(CaptureNotFound):
                reopened.get(old_id + 1)

    def test_direct_publish_rejects_impossible_evidence(self):
        evidence = CaptureEvidence(self.plan, (
            FrameEvent(2, 'a', 1, 0), FrameEvent(3, 'b', 1, 0),
            FrameEvent(4, 'c', 1, 1), FrameEvent(5, 'd', 1, 1),
        ))
        with SqliteArchive.open(self.database) as archive:
            with self.assertRaises(NoExplanation):
                archive.publish(evidence)
            with self.assertRaises(CaptureNotFound):
                archive.get(1)
