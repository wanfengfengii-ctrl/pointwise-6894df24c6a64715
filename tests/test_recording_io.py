import json
import tempfile
import unittest
from pathlib import Path

from archive.artifacts import CaptureNotFound
from archive.collect import capture
from archive.storage import SqliteArchive
from plans.registry import PlanRegistry
from recordings.batches import read_recording
from recordings.events import EndEvent, FrameEvent, RecordingError, StartEvent
from recordings.source import RecordingSource
from tests.test_capture import write_recording


class RecordingIOTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'recording.jsonl'
        self.plan = PlanRegistry().register('station-12', 2, {})
        self.start = {'type': 'start', 'key': 'station-12', 'bit_width': 2}
        self.frame = {'type': 'frame', 'frame_id': 'a', 'mask': 1, 'value': 0}
        self.end = {'type': 'end'}

    def write_events(self, events):
        lines = [event if isinstance(event, str) else json.dumps(event) for event in events]
        self.path.write_text(''.join(line + '\n' for line in lines), encoding='utf-8')

    def assert_rejected_recording(self, archive, line):
        with RecordingSource(self.path) as source:
            with self.assertRaises(RecordingError) as caught:
                capture(archive, self.plan, read_recording(source))
        self.assertEqual(caught.exception.line, line)
        with self.assertRaises(CaptureNotFound):
            archive.get(1)

    def test_event_positions_and_explicit_close(self):
        write_recording(self.path, (('a', 1, 0),))
        with RecordingSource(self.path) as source:
            self.assertEqual(list(read_recording(source)), [
                StartEvent(1, 'station-12', 2), FrameEvent(2, 'a', 1, 0), EndEvent(3),
            ])
        source.close()
        with self.assertRaises(RecordingError):
            next(source.iter_lines())
        with RecordingSource(self.path) as partial:
            raw = partial.iter_lines()
            self.assertEqual(next(raw)[0], 1)
            partial.close()
            with self.assertRaises(RecordingError) as caught:
                next(raw)
            self.assertEqual(caught.exception.line, 2)

    def test_malformed_event(self):
        invalid_frames = (
            '{', 'null', '[]', '',
            '{"type":"frame","frame_id":"a","mask":1,"mask":2,"value":0}',
            '{"type":"frame","frame_id":"a","mask":1,"value":NaN}',
            {'type': 'frame', 'frame_id': 'a', 'mask': 1},
            {**self.frame, 'frame_id': None}, {**self.frame, 'frame_id': ''},
            {**self.frame, 'mask': None}, {**self.frame, 'mask': 0}, {**self.frame, 'mask': 4},
            {**self.frame, 'mask': 1.0}, {**self.frame, 'mask': True},
            {**self.frame, 'value': None}, {**self.frame, 'value': 1.0},
            {**self.frame, 'value': False}, {**self.frame, 'value': 2},
        )
        with SqliteArchive.open(self.root / 'bad.sqlite3') as archive:
            for event in invalid_frames:
                with self.subTest(event=event):
                    self.write_events((self.start, event, self.end))
                    self.assert_rejected_recording(archive, 2)
            for width in (None, True, 2.0, 0, 65):
                with self.subTest(width=width):
                    self.write_events(({**self.start, 'bit_width': width}, self.end))
                    self.assert_rejected_recording(archive, 1)
            self.write_events(({**self.start, 'key': None}, self.end))
            self.assert_rejected_recording(archive, 1)

    def test_invalid_boundary(self):
        cases = (
            ((), 1), ((self.frame, self.end), 1), ((self.end,), 1),
            ((self.start, self.start, self.end), 2), ((self.start, self.frame), 3),
            ((self.start, self.end, self.frame), 3), ((self.start, self.end, self.end), 3),
            ((self.start, self.end, ''), 3),
        )
        with SqliteArchive.open(self.root / 'boundary.sqlite3') as archive:
            for events, line in cases:
                with self.subTest(events=events):
                    self.write_events(events)
                    self.assert_rejected_recording(archive, line)
        self.write_events((self.start, self.end, self.frame))
        with RecordingSource(self.path) as source:
            stream = read_recording(source)
            self.assertIsInstance(next(stream), StartEvent)
            with self.assertRaises(RecordingError) as caught:
                next(stream)
            self.assertEqual(caught.exception.line, 3)

    def test_utf8_read_error_keeps_line_position(self):
        self.path.write_bytes((json.dumps(self.start) + '\n').encode('utf-8') + b'\xff\n')
        with RecordingSource(self.path) as source:
            stream = read_recording(source)
            self.assertEqual(next(stream), StartEvent(1, 'station-12', 2))
            with self.assertRaises(RecordingError) as caught:
                next(stream)
        self.assertEqual(caught.exception.line, 2)

    def test_missing_path_is_recording_error(self):
        with RecordingSource(self.path) as source:
            with self.assertRaises(RecordingError) as caught:
                list(read_recording(source))
        self.assertIsNone(caught.exception.line)
