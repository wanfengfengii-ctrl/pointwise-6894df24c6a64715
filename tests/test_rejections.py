import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from contextlib import closing
from pathlib import Path

from admission.capture import collect_evidence
from archive.artifacts import CaptureNotFound
from archive.certificate import CertificateError, build_certificate, validate_certificate
from archive.collect import CaptureRejected
from archive.feasibility import NoExplanation
from archive.storage import SqliteArchive
from plans.registry import PlanRegistry
from recordings.batches import read_recording
from recordings.source import RecordingSource
from recordings.events import FrameEvent, RecordingError
from review.export import ExportError, export, export_rejection
from review.query import query
from review.verification import verify_rejection
from rejection_fixture import deny_sqlite_commit
from tests.test_capture import capture_file, write_recording


SAMPLE = Path(__file__).resolve().parent.parent / 'samples' / 'rejection.jsonl'
SAMPLE_PLAN = ('station-proof', 3, {'x': 1, 'y': 2, 'z': 4})
SAMPLE_FRAMES = (
    (2, 'a', 1, 0), (3, 'b', 2, 0), (4, 'c', 3, 1),
    (5, 'd', 4, 0), (6, 'e', 6, 1), (7, 'f', 5, 1),
)


def diagnostic_count(database):
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute('SELECT COUNT(*) FROM diagnostics').fetchone()[0]


def diagnostic_row(database, diagnostic_id):
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            'SELECT evidence, binding FROM diagnostics WHERE diagnostic_id = ?', (diagnostic_id,)
        ).fetchone()


def record_of(certificate, groups=None):
    return {
        'diagnostic_id': certificate['diagnostic_id'],
        'groups': list(certificate['groups'] if groups is None else groups),
    }


class RejectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / 'rejections.sqlite3'
        self.recording = self.root / 'source.jsonl'
        self.plan = PlanRegistry().register(*SAMPLE_PLAN)

    def capture_frames(self, archive, frames, *, key='station-12', width=2):
        path = write_recording(self.recording, frames, key=key, width=width)
        return capture_file(archive, self.plan_for(key, width), path)

    def plan_for(self, key, width):
        return PlanRegistry().register(key, width, {})

    def reject(self, archive, frames, *, key='station-12', width=2):
        path = write_recording(self.recording, frames, key=key, width=width)
        with self.assertRaises(CaptureRejected) as caught:
            capture_file(archive, self.plan_for(key, width), path)
        return caught.exception

    # 采集与提交边界

    def test_sample_diagnostic_and_certificate(self):
        with SqliteArchive.open(self.database) as archive:
            with RecordingSource(SAMPLE) as source:
                with self.assertRaises(CaptureRejected) as caught:
                    capture_file(archive, self.plan, SAMPLE)
            diagnostic_id = caught.exception.diagnostic_id
            self.assertIsInstance(diagnostic_id, str)
            self.assertTrue(diagnostic_id)
            self.assertIsInstance(caught.exception.__cause__, NoExplanation)
            certificate = export_rejection(archive, diagnostic_id)
            self.assertEqual(certificate['diagnostic_id'], diagnostic_id)
            self.assertEqual(certificate['groups'], [['a', 'b', 'c'], ['b', 'd', 'e'], ['a', 'd', 'f']])
            self.assertTrue(verify_rejection(archive, certificate, diagnostic_id))
            frozen = archive.get_diagnostic(diagnostic_id)
            self.assertEqual(
                [(frame.line, frame.frame_id, frame.mask, frame.value) for frame in frozen.evidence.frames],
                list(SAMPLE_FRAMES),
            )

    def test_success_capture_has_no_diagnostic_id(self):
        with SqliteArchive.open(self.database) as archive:
            capture_id = self.capture_frames(archive, (('a', 1, 0), ('b', 1, 0)))
            self.assertEqual(query(archive, capture_id, 1), [(0,)])
            self.assertEqual(diagnostic_count(self.database), 0)

    def test_other_rejections_carry_no_diagnostic_id(self):
        with SqliteArchive.open(self.database) as archive:
            self.assertIsNone(self.reject(archive, (('a', 1, 0), ('a', 1, 1))).diagnostic_id)
            self.assertIsNone(self.reject(archive, (('a', 1, 0), ('a', 2, 0))).diagnostic_id)
            write_recording(self.recording, (('a', 1, 0),), end=False)
            with RecordingSource(self.recording) as source:
                with self.assertRaises(RecordingError):
                    capture_file(archive, self.plan_for('station-12', 2), self.recording)
            self.assertEqual(diagnostic_count(self.database), 0)

    def test_diagnostic_commit_failure_reports_but_withholds_id(self):
        with SqliteArchive.open(self.database) as archive:
            old = self.capture_frames(archive, (('a', 1, 0), ('b', 1, 0)))
            old_record = export(archive, old, 1)
        with closing(sqlite3.connect(self.database, isolation_level=None)) as connection:
            wrapped = SqliteArchive(connection)
            with deny_sqlite_commit(connection) as denied:
                rejected = self.reject(wrapped, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            self.assertEqual(denied, ['COMMIT'])
            self.assertIsNone(rejected.diagnostic_id)
        with SqliteArchive.open(self.database) as reopened:
            self.assertEqual(export(reopened, old, 1), old_record)
            self.assertEqual(diagnostic_count(self.database), 0)

    def test_diagnostic_insert_failure_leaves_no_partial_row(self):
        with SqliteArchive.open(self.database) as archive:
            old = self.capture_frames(archive, (('a', 1, 0), ('b', 1, 0)))
        with closing(sqlite3.connect(self.database)) as editor:
            editor.execute(
                "CREATE TRIGGER reject_diagnostic BEFORE INSERT ON diagnostics "
                "BEGIN SELECT RAISE(ABORT, 'diagnostic refused'); END"
            )
            editor.commit()
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            self.assertIsNone(rejected.diagnostic_id)
            self.assertEqual(diagnostic_count(self.database), 0)
            self.assertEqual(query(archive, old, 1), [(0,)])

    def test_existing_captures_survive_rejection(self):
        with SqliteArchive.open(self.database) as archive:
            good = self.capture_frames(archive, (('a', 1, 0), ('b', 1, 0)))
            good_record = export(archive, good, 1)
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            self.assertEqual(export(archive, good, 1), good_record)
            self.assertEqual(query(archive, good, 1), [(0,)])
            with self.assertRaises(CaptureNotFound):
                archive.get(good + 1)
        self.assertIsNotNone(rejected.diagnostic_id)

    def test_version_one_archive_still_readable(self):
        legacy = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute('CREATE TABLE captures (capture_id INTEGER PRIMARY KEY, evidence TEXT NOT NULL)')
            connection.execute('PRAGMA user_version = 1')
            connection.execute(
                'INSERT INTO captures (evidence) VALUES (?)',
                (json.dumps(
                    {'plan': {'key': 'station-12', 'bit_width': 2, 'loops': [['x', 1], ['y', 2]]},
                     'frames': [[2, 'a', 1, 0], [3, 'b', 1, 0]]},
                    separators=(',', ':'),
                ),),
            )
            connection.commit()
        with SqliteArchive.open(legacy) as archive:
            self.assertEqual(query(archive, 1, 1), [(0,)])
            with RecordingSource(SAMPLE) as source:
                with self.assertRaises(CaptureRejected) as caught:
                    capture_file(archive, self.plan, SAMPLE)
            self.assertIsInstance(caught.exception.diagnostic_id, str)
            self.assertTrue(verify_rejection(archive, export_rejection(archive, caught.exception.diagnostic_id), caught.exception.diagnostic_id))

    # 复核边界

    def test_wrong_and_missing_diagnostics(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            certificate = export_rejection(archive, rejected.diagnostic_id)
            self.assertFalse(verify_rejection(archive, certificate, rejected.diagnostic_id + 'x'))
            self.assertFalse(verify_rejection(archive, record_of(certificate, []), rejected.diagnostic_id + 'x'))
            forged = record_of(certificate)
            forged['diagnostic_id'] = 'forged'
            self.assertFalse(verify_rejection(archive, forged, rejected.diagnostic_id))
            with self.assertRaises(ExportError):
                export_rejection(archive, 'missing')
            with self.assertRaises(ExportError):
                export_rejection(archive, None)

    def test_certificate_contract(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            certificate = export_rejection(archive, rejected.diagnostic_id)
            self.assertTrue(verify_rejection(archive, certificate, rejected.diagnostic_id))
            groups = certificate['groups']
            invalid = (
                groups[:1],
                [['a', 'b', 'c', 'd']],
                [['a', 'b', 'c', 'c']],
                [['a', 'b', 'unknown']],
                [['a', 'b']],
                [['a', 'b', 'c'], []],
                [],
                [['a', 'b', 'c'], ['a', 'd', 'f']],
            )
            for broken in invalid:
                with self.subTest(groups=broken):
                    self.assertFalse(verify_rejection(archive, record_of(certificate, broken), rejected.diagnostic_id))

    def test_alternative_certificates_accepted(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            certificate = export_rejection(archive, rejected.diagnostic_id)
            swapped = record_of(certificate, [list(reversed(group)) for group in reversed(certificate['groups'])])
            self.assertTrue(verify_rejection(archive, swapped, rejected.diagnostic_id))
            self.assertFalse(verify_rejection(archive, {'groups': certificate['groups']}, rejected.diagnostic_id))

    def test_bad_evidence_rejected(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            certificate = export_rejection(archive, rejected.diagnostic_id)
            self.assertTrue(verify_rejection(archive, certificate, rejected.diagnostic_id))
            frames = archive.get_diagnostic(rejected.diagnostic_id).evidence.frames
            original = diagnostic_row(self.database, rejected.diagnostic_id)
        # 换成另一份语法有效的证据：只改首次位置，证书仍能通过异或验算。
        body = json.loads(original[0])
        body['frames'][0][0] = body['frames'][0][0] + 100
        tampered_evidence = tuple(FrameEvent(*row) for row in body['frames'])
        self.assertTrue(validate_certificate(tampered_evidence, certificate['groups']))
        tampered = json.dumps(body, ensure_ascii=True, separators=(',', ':'))
        with closing(sqlite3.connect(self.database)) as editor:
            editor.execute(
                'UPDATE diagnostics SET evidence = ? WHERE diagnostic_id = ?',
                (tampered, rejected.diagnostic_id),
            )
            editor.commit()
        with SqliteArchive.open(self.database) as archive:
            with self.assertRaises(ExportError):
                export_rejection(archive, rejected.diagnostic_id)
            self.assertFalse(verify_rejection(archive, certificate, rejected.diagnostic_id))
        self.assertEqual(
            [(frame.line, frame.frame_id) for frame in frames],
            [(2, 'a'), (3, 'b'), (4, 'c'), (5, 'd')],
        )

    def test_corrupted_evidence_rejected(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
        with closing(sqlite3.connect(self.database)) as editor:
            editor.execute('UPDATE diagnostics SET evidence = ? WHERE diagnostic_id = ?', ('{', rejected.diagnostic_id))
            editor.commit()
        with SqliteArchive.open(self.database) as archive:
            with self.assertRaises(ExportError):
                export_rejection(archive, rejected.diagnostic_id)
            self.assertFalse(verify_rejection(archive, {'diagnostic_id': rejected.diagnostic_id,
                                                         'groups': [['a', 'b', 'c']]}, rejected.diagnostic_id))

    def test_repeated_and_independent_frames(self):
        with SqliteArchive.open(self.database) as archive:
            # 一位声明 a=0、b=1、c=1，重投同 ID 的 b=1 仍可接纳。
            accepted = self.capture_frames(archive, (('a', 1, 0), ('b', 1, 1), ('c', 1, 1), ('b', 1, 1)),
                                           key='one-bit', width=1)
            # 状态 1 只与 a=0 冲突一次，仍在至多一次失配内。
            self.assertEqual(query(archive, accepted, 1), [(1,)])
            self.assertEqual(len(archive.get(accepted).evidence.frames), 3)
            # 另一批增加独立帧 d=0 后无解并给出可复核诊断。
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 1), ('c', 1, 1), ('d', 1, 0)),
                                   key='one-bit', width=1)
            self.assertIsInstance(rejected.diagnostic_id, str)
            certificate = export_rejection(archive, rejected.diagnostic_id)
            self.assertTrue(verify_rejection(archive, certificate, rejected.diagnostic_id))
            common = None
            for group in certificate['groups']:
                common = set(group) if common is None else common & set(group)
            self.assertEqual(common, set())

    def test_reopen_export_and_verify(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            diagnostic_id = rejected.diagnostic_id
            certificate = export_rejection(archive, diagnostic_id)
        with SqliteArchive.open(self.database) as reopened:
            self.assertEqual(export_rejection(reopened, diagnostic_id), certificate)
            self.assertTrue(verify_rejection(reopened, certificate, diagnostic_id))

    def test_sample_joint_verification_after_reopen(self):
        source = self.root / 'handover-source.jsonl'
        source.write_bytes(SAMPLE.read_bytes())
        with SqliteArchive.open(self.database) as archive:
            with RecordingSource(source):
                with self.assertRaises(CaptureRejected) as caught:
                    capture_file(archive, self.plan, source)
            diagnostic_id = caught.exception.diagnostic_id
            self.assertIsNotNone(diagnostic_id)
            sealed = [
                [frame.line, frame.frame_id, frame.mask, frame.value]
                for frame in archive.get_diagnostic(diagnostic_id).evidence.frames
            ]
            self.assertEqual(sealed, [list(row) for row in SAMPLE_FRAMES])
            first_certificate = export_rejection(archive, diagnostic_id)
        source.unlink()
        with SqliteArchive.open(self.database) as reopened:
            certificate = export_rejection(reopened, diagnostic_id)
            self.assertEqual(certificate, first_certificate)
            self.assertEqual(certificate['groups'], [['a', 'b', 'c'], ['b', 'd', 'e'], ['a', 'd', 'f']])
            self.assertTrue(verify_rejection(reopened, certificate, diagnostic_id))
            self.assertFalse(verify_rejection(reopened, record_of(certificate, certificate['groups'][:2]),
                                              diagnostic_id))

    def test_diagnostic_keeps_first_frame_position_on_replay(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (
                ('a', 1, 0), ('b', 1, 1), ('a', 1, 0), ('c', 1, 1), ('d', 1, 0), ('b', 1, 1),
            ), key='one-bit', width=1)
            frozen = archive.get_diagnostic(rejected.diagnostic_id)
            self.assertEqual(
                [(frame.line, frame.frame_id) for frame in frozen.evidence.frames],
                [(2, 'a'), (3, 'b'), (5, 'c'), (6, 'd')],
            )
            certificate = export_rejection(archive, rejected.diagnostic_id)
            self.assertTrue(verify_rejection(archive, certificate, rejected.diagnostic_id))

    def test_certificate_is_json_serializable(self):
        with SqliteArchive.open(self.database) as archive:
            rejected = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            certificate = export_rejection(archive, rejected.diagnostic_id)
            self.assertEqual(set(certificate), {'diagnostic_id', 'groups'})
            self.assertIsInstance(certificate['diagnostic_id'], str)
            for group in certificate['groups']:
                self.assertIsInstance(group, list)
                self.assertTrue(group)
                self.assertTrue(all(isinstance(frame_id, str) for frame_id in group))
            round_tripped = json.loads(json.dumps(certificate))
            self.assertEqual(round_tripped, certificate)
            self.assertTrue(verify_rejection(archive, round_tripped, rejected.diagnostic_id))

    def test_diagnostic_id_collision_retries_with_new_id(self):
        with SqliteArchive.open(self.database) as archive:
            first = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            ids = iter((first.diagnostic_id, 'fresh-diagnostic-id'))
            with unittest.mock.patch('archive.collect._new_diagnostic_id', lambda: next(ids)):
                with RecordingSource(write_recording(
                    self.recording, (('e', 1, 0), ('f', 1, 0), ('g', 1, 1), ('h', 1, 1)),
                )) as source:
                    with self.assertRaises(CaptureRejected) as caught:
                        capture_file(archive, self.plan_for('station-12', 2), self.recording)
            self.assertEqual(caught.exception.diagnostic_id, 'fresh-diagnostic-id')
            certificate = export_rejection(archive, 'fresh-diagnostic-id')
            self.assertTrue(verify_rejection(archive, certificate, 'fresh-diagnostic-id'))
            self.assertTrue(verify_rejection(
                archive, export_rejection(archive, first.diagnostic_id), first.diagnostic_id
            ))

    def test_distinct_diagnostic_ids(self):
        with SqliteArchive.open(self.database) as archive:
            first = self.reject(archive, (('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
            second = self.reject(archive, (('e', 1, 0), ('f', 1, 0), ('g', 1, 1), ('h', 1, 1)))
            self.assertNotEqual(first.diagnostic_id, second.diagnostic_id)
            for rejected in (first, second):
                certificate = export_rejection(archive, rejected.diagnostic_id)
                self.assertTrue(verify_rejection(archive, certificate, rejected.diagnostic_id))

    def test_build_certificate_requires_impossible_batch(self):
        with SqliteArchive.open(self.database) as archive:
            capture_id = self.capture_frames(archive, (('a', 1, 0), ('b', 2, 0), ('c', 3, 1)))
            frames = archive.get(capture_id).evidence.frames
        self.assertFalse(validate_certificate(frames, [['a', 'b', 'c']]))
        with self.assertRaises(CertificateError):
            build_certificate(frames)


class CertificateContractTests(unittest.TestCase):
    def test_sample_certificate_passes_contract(self):
        plan = PlanRegistry().register(*SAMPLE_PLAN)
        with RecordingSource(SAMPLE) as source:
            evidence = collect_evidence(plan, read_recording(source))
        groups = build_certificate(evidence.frames)
        self.assertEqual([list(group) for group in groups], [['a', 'b', 'c'], ['b', 'd', 'e'], ['a', 'd', 'f']])
        self.assertTrue(validate_certificate(evidence.frames, [list(group) for group in groups]))
        self.assertFalse(validate_certificate(evidence.frames, [list(group) for group in groups[:2]]))
