import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from admission.capture import collect_evidence
from admission.frames import AdmissionOutcome, FrameConflict
from admission.session import CaptureSession, SessionError, SessionState
from archive.artifacts import CaptureNotFound
from archive.collect import CaptureRejected, capture
from archive.feasibility import NoExplanation
from archive.storage import SqliteArchive
from plans.model import PlanConflict
from plans.registry import PlanRegistry
from recordings.batches import read_recording
from recordings.events import EndEvent, FrameEvent, RecordingError, StartEvent
from recordings.source import RecordingSource
from review.query import query


def write_recording(path, frames, *, key='station-12', width=2, end=True):
    events = [{'type': 'start', 'key': key, 'bit_width': width}]
    events.extend(
        {'type': 'frame', 'frame_id': frame_id, 'mask': mask, 'value': value}
        for frame_id, mask, value in frames
    )
    if end:
        events.append({'type': 'end'})
    path.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
    return path


def capture_file(archive, plan, path, **options):
    with RecordingSource(path) as source:
        return capture(archive, plan, read_recording(source), **options)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.recording = self.root / 'current.jsonl'
        self.archive = SqliteArchive.open(self.root / 'capture.sqlite3')
        self.addCleanup(self.archive.close)
        self.registry = PlanRegistry()
        self.plan = self.registry.register('station-12', 2, {'x': 1, 'y': 2})

    def publish(self, frames, **recording_options):
        path = write_recording(self.recording, frames, **recording_options)
        return capture_file(self.archive, self.plan, path)

    def test_repeat_plan(self):
        registered = self.registry.register('station-12', 2, {'y': 2, 'x': 1})
        self.assertIs(registered, self.plan)
        self.assertEqual(registered.catalog.get_mask('x'), 1)
        self.assertEqual(registered.catalog.items(), (('x', 1), ('y', 2)))
        capture_id = self.publish((('a', 1, 0), ('b', 1, 0), ('c', 2, 1), ('d', 2, 1)))
        self.assertEqual(self.archive.get(capture_id).plan, self.plan)
        self.assertEqual(query(self.archive, capture_id, 1, 2), [(0, 1)])

    def test_start_binding(self):
        frames = (('a', 1, 0),)
        old_id = self.publish(frames)
        for overrides in ({'key': 'other-station'}, {'width': 3}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(CaptureRejected) as caught:
                    self.publish(frames, **overrides)
                self.assertIsInstance(caught.exception.__cause__, SessionError)
                with self.assertRaises(CaptureNotFound):
                    self.archive.get(old_id + 1)
        self.assertEqual(self.archive.get(old_id).plan, self.plan)

    def test_plan_conflict(self):
        for width, loops in ((3, {'x': 1, 'y': 2}), (2, {'x': 2, 'y': 1})):
            with self.subTest(width=width, loops=loops):
                with self.assertRaises(PlanConflict):
                    self.registry.register('station-12', width, loops)
                self.assertIs(self.registry.get('station-12'), self.plan)
        capture_id = self.publish((('a', 2, 0), ('b', 2, 0)))
        self.assertEqual(query(self.archive, capture_id, 2), [(0,)])

    def test_invalid_declaration(self):
        invalid = (
            (0, {}), (65, {}), (2.0, {}), (True, {}), (None, {}),
            (2, {'x': 0}), (2, {'x': 4}), (2, {'x': -1}), (2, {'x': 1.0}),
            (2, {'x': True}), (2, {'x': None}), (2, {'': 1}), (2, None),
        )
        for width, loops in invalid:
            with self.subTest(width=width, loops=loops):
                with self.assertRaises(PlanConflict):
                    self.registry.register('unassigned', width, loops)
                with self.assertRaises(KeyError):
                    self.registry.get('unassigned')
        with self.assertRaises(PlanConflict):
            self.registry.register('', 2, {})
        with self.assertRaises(PlanConflict):
            self.registry.register('unassigned', 2)
        with self.assertRaises(PlanConflict):
            self.registry.register('station-12', 2, {'x': 0})
        self.assertIs(self.registry.get('station-12'), self.plan)
        new_plan = self.registry.register('unassigned', 1, {})
        self.assertEqual(new_plan.catalog.items(), ())
        old_id = self.publish((('a', 1, 1), ('b', 1, 1)))
        self.assertEqual(query(self.archive, old_id, 1), [(1,)])

    def test_frame_repeat(self):
        base = (('a', 1, 0), ('b', 2, 0), ('c', 3, 1))
        original = self.publish(base)
        replayed = self.publish(base + (base[0],))
        self.assertEqual(self.archive.get(original).evidence.frames, self.archive.get(replayed).evidence.frames)
        expected = [(0, 0), (0, 1), (1, 0)]
        self.assertEqual(query(self.archive, original, 1, 2), expected)
        self.assertEqual(query(self.archive, replayed, 1, 2), expected)

    def test_independent_evidence(self):
        rows = (('a', 1, 0), ('b', 2, 0), ('a', 1, 0), ('d', 1, 0))
        path = write_recording(self.recording, rows)
        with RecordingSource(path) as source:
            evidence = collect_evidence(self.plan, read_recording(source))
        expected = [('a', 2), ('b', 3), ('d', 5)]
        self.assertEqual([(frame.frame_id, frame.line) for frame in evidence.frames], expected)
        capture_id = capture_file(self.archive, self.plan, path)
        self.assertEqual(self.archive.get(capture_id).evidence, evidence)

    def test_sealed_evidence(self):
        original = FrameEvent(2, 'a', 1, 0)
        repeat = FrameEvent(3, 'a', 1, 0)
        independent = FrameEvent(4, 'b', 1, 0)
        session = CaptureSession.start(self.plan, StartEvent(1, self.plan.key, 2))
        self.assertIs(session.state, SessionState.ACCEPTING)
        self.assertIs(session.accept_frame(original), AdmissionOutcome.ACCEPTED)
        self.assertIs(session.accept_frame(repeat), AdmissionOutcome.REPLAYED)
        self.assertIs(session.accept_frame(independent), AdmissionOutcome.ACCEPTED)
        snapshot = session.finish(EndEvent(5))
        self.assertIs(session.state, SessionState.FINISHED)
        with self.assertRaises(SessionError):
            session.accept_frame(FrameEvent(6, 'c', 2, 0))
        with self.assertRaises(SessionError):
            session.finish(EndEvent(6))
        with self.assertRaises(FrozenInstanceError):
            snapshot.frames = ()
        self.assertEqual(snapshot.frames, (original, independent))

    def test_missing_end(self):
        rows = (('a', 1, 0), ('b', 1, 0))
        old_id = self.publish(rows)
        with self.assertRaises(RecordingError) as caught:
            self.publish(rows, end=False)
        self.assertEqual(caught.exception.line, 4)
        with self.assertRaises(CaptureNotFound):
            self.archive.get(old_id + 1)
        self.assertEqual(query(self.archive, old_id, 1), [(0,)])

    def test_session_aborted_on_invalid_frame(self):
        session = CaptureSession.start(self.plan, StartEvent(1, self.plan.key, 2))
        with self.assertRaises(RecordingError) as caught:
            session.accept_frame(FrameEvent(2, 'a', 4, 0))
        self.assertEqual(caught.exception.line, 2)
        self.assertIs(session.state, SessionState.ABORTED)
        with self.assertRaises(SessionError):
            session.finish(EndEvent(3))

    def test_frame_conflict(self):
        previous = self.publish((('a', 1, 1), ('b', 1, 1)))
        with self.assertRaises(CaptureRejected) as caught:
            self.publish((('duplicate', 1, 0), ('duplicate', 1, 1)))
        conflict = caught.exception.__cause__
        self.assertIsInstance(conflict, FrameConflict)
        self.assertEqual((conflict.previous.line, conflict.incoming.line), (2, 3))
        with self.assertRaises(CaptureNotFound):
            self.archive.get(previous + 1)
        self.assertEqual(query(self.archive, previous, 1), [(1,)])

    def test_changed_mask(self):
        with self.assertRaises(CaptureRejected) as caught:
            self.publish((('a', 1, 0), ('a', 2, 0)))
        self.assertIsInstance(caught.exception.__cause__, FrameConflict)
        with self.assertRaises(CaptureNotFound):
            self.archive.get(1)
        session = CaptureSession.start(self.plan, StartEvent(1, self.plan.key, 2))
        session.accept_frame(FrameEvent(2, 'a', 1, 0))
        with self.assertRaises(FrameConflict):
            session.accept_frame(FrameEvent(3, 'a', 2, 0))
        self.assertIs(session.state, SessionState.REJECTED)
        with self.assertRaises(SessionError):
            session.finish(EndEvent(4))

    def test_impossible_batch(self):
        previous = self.publish((('a', 1, 0), ('b', 1, 0)))
        with self.assertRaises(CaptureRejected) as caught:
            self.publish((('a', 1, 0), ('b', 1, 0), ('c', 1, 1), ('d', 1, 1)))
        self.assertIsInstance(caught.exception.__cause__, NoExplanation)
        with self.assertRaises(CaptureNotFound):
            self.archive.get(previous + 1)
        self.assertEqual(query(self.archive, previous, 1), [(0,)])

    def test_global_budget(self):
        rows = (('a', 1, 0), ('b', 1, 1), ('c', 2, 0), ('d', 2, 1))
        previous = self.publish(rows[:2])
        with self.assertRaises(CaptureRejected) as caught:
            self.publish(rows)
        self.assertIsInstance(caught.exception.__cause__, NoExplanation)
        with self.assertRaises(CaptureNotFound):
            self.archive.get(previous + 1)
        self.assertEqual(query(self.archive, previous, 1), [(0,), (1,)])
