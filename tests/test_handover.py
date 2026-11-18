import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archive.artifacts import CaptureNotFound
from archive.collect import CaptureRejected
from archive.storage import ArchiveError, SqliteArchive
from plans.registry import PlanRegistry
from review.export import ExportError, export
from review.query import query
from review.verification import verify_export
from tests.test_capture import capture_file, write_recording
from tests.test_review import FOUR, THREE


class HandoverTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / 'handover.sqlite3'
        self.recording = self.root / 'source.jsonl'
        self.plan = PlanRegistry().register('station-12', 2, {'x': 1, 'y': 2})

    def publish(self, archive, frames, **options):
        path = write_recording(self.recording, frames)
        return capture_file(archive, self.plan, path, **options)

    def test_export_after_reopen(self):
        with SqliteArchive.open(self.database) as archive:
            capture_id = self.publish(archive, FOUR + (FOUR[0],))
            report = export(archive, capture_id, 1, 2)
            self.assertEqual(query(archive, capture_id, 1, 2), [(0, 0), (0, 1)])
        self.recording.unlink()
        with SqliteArchive.open(self.database) as reopened:
            self.assertEqual(query(reopened, capture_id, 1, 2), [(0, 0), (0, 1)])
            self.assertEqual(export(reopened, capture_id, 1, 2), report)
            self.assertEqual(report.as_dict(), {
                'capture_id': capture_id, 'sampling_key': 'station-12', 'bit_width': 2,
                'masks': [1, 2], 'combinations': [[0, 0], [0, 1]],
            })
            self.assertTrue(verify_export(reopened, report, capture_id, 1, 2).matches)

    def test_capture_identity(self):
        with SqliteArchive.open(self.database) as archive:
            first_id = self.publish(archive, THREE)
            second_id = self.publish(archive, FOUR)
            first_record = export(archive, first_id, 1, 2)
            second_record = export(archive, second_id, 1, 2)
        self.assertNotEqual(first_id, second_id)
        self.recording.unlink()
        with SqliteArchive.open(self.database) as reopened:
            self.assertEqual(query(reopened, first_id, 1, 2), [(0, 0), (0, 1), (1, 0)])
            self.assertEqual(query(reopened, second_id, 1, 2), [(0, 0), (0, 1)])
            self.assertEqual(export(reopened, first_id, 1, 2), first_record)
            self.assertEqual(export(reopened, second_id, 1, 2), second_record)
            wrong_capture = verify_export(reopened, first_record, second_id, 1, 2)
            self.assertEqual(wrong_capture.mismatches, ('capture_id', 'combinations'))

    def test_high_bit_roundtrip(self):
        high = 1 << 63
        plan = PlanRegistry().register('wide', 64, {'highest': high, 'lowest': 1})
        path = write_recording(self.recording, (('a', high, 1), ('b', high, 1)), key='wide', width=64)
        with SqliteArchive.open(self.database) as archive:
            capture_id = capture_file(archive, plan, path)
            report = export(archive, capture_id, high, 1)
        path.unlink()
        with SqliteArchive.open(self.database) as reopened:
            frozen = reopened.get(capture_id)
            self.assertEqual(frozen.plan, plan)
            self.assertEqual([frame.mask for frame in frozen.evidence.frames], [high, high])
            self.assertEqual(query(reopened, capture_id, high, 1), [(1, 0), (1, 1)])
            self.assertEqual(report.masks, (high, 1))
            self.assertEqual(report.combinations, ((1, 0), (1, 1)))
            self.assertEqual(export(reopened, capture_id, high, 1), report)

    def test_invalid_export(self):
        with SqliteArchive.open(self.database) as archive:
            capture_id = self.publish(archive, FOUR)
            for requested_id, masks in ((capture_id + 1, (1,)), (capture_id, (4,)), (capture_id, ()), (capture_id, (None,))):
                with self.subTest(requested_id=requested_id, masks=masks):
                    with self.assertRaises(ExportError):
                        export(archive, requested_id, *masks)

    def test_verify_record(self):
        with SqliteArchive.open(self.database) as archive:
            capture_id = self.publish(archive, FOUR)
            report = export(archive, capture_id, 1, 2)
            self.assertTrue(verify_export(archive, report, capture_id, 1, 2).matches)
            changes = (
                ('masks', (2, 1)), ('sampling_key', 'unknown'), ('bit_width', 3),
                ('capture_id', capture_id + 1), ('combinations', ((0, 0), (1, 0))),
                ('combinations', ((False, 0), (0, 1))),
            )
            for field, value in changes:
                with self.subTest(field=field, value=value):
                    result = verify_export(archive, replace(report, **{field: value}), capture_id, 1, 2)
                    self.assertFalse(result.matches)
                    self.assertEqual(result.mismatches, (field,))
            self.assertEqual(verify_export(archive, report, capture_id + 1, 1, 2).mismatches, ('capture',))
            self.assertEqual(verify_export(archive, report, capture_id, 0).mismatches, ('masks',))

    def test_archive_rollback(self):
        candidates = []

        def interrupt(candidate_id):
            candidates.append(candidate_id)
            raise OSError('值班员停止提交')

        with SqliteArchive.open(self.database) as archive:
            previous = self.publish(archive, THREE)
            old_record = export(archive, previous, 1, 2)
            with self.assertRaisesRegex(CaptureRejected, '值班员停止提交') as caught:
                self.publish(archive, FOUR, before_commit=interrupt)
            self.assertIsInstance(caught.exception.__cause__, ArchiveError)
            self.assertEqual(len(candidates), 1)
            with self.assertRaises(CaptureNotFound):
                archive.get(candidates[0])
        with SqliteArchive.open(self.database) as reopened:
            self.assertEqual(export(reopened, previous, 1, 2), old_record)
            with self.assertRaises(CaptureNotFound):
                reopened.get(candidates[0])

    def test_precommit_visibility(self):
        staged = []
        observations = []

        def inspect_and_interrupt(candidate_id):
            staged.append(candidate_id)
            with SqliteArchive.open(self.database) as observer:
                previous_values = query(observer, previous, 1, 2)
                try:
                    observer.get(candidate_id)
                except CaptureNotFound:
                    visible = False
                else:
                    visible = True
                observations.append((previous_values, visible))
            raise OSError('结束预提交检查')

        with SqliteArchive.open(self.database) as archive:
            previous = self.publish(archive, THREE)
            old_record = export(archive, previous, 1, 2)
            with self.assertRaisesRegex(CaptureRejected, '结束预提交检查'):
                self.publish(archive, FOUR, before_commit=inspect_and_interrupt)
        self.assertEqual(len(staged), 1)
        self.assertEqual(observations, [([(0, 0), (0, 1), (1, 0)], False)])
        with SqliteArchive.open(self.database) as reopened:
            self.assertEqual(export(reopened, previous, 1, 2), old_record)
            with self.assertRaises(CaptureNotFound):
                reopened.get(staged[0])
