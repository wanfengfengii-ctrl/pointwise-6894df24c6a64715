import tempfile
import unittest
from itertools import permutations, product
from pathlib import Path

from archive.collect import CaptureRejected
from archive.feasibility import NoExplanation
from archive.storage import SqliteArchive
from plans.registry import PlanRegistry
from review.parameters import QueryParameterError
from review.query import query
from tests.test_capture import capture_file, write_recording


THREE = (('a', 1, 0), ('b', 2, 0), ('c', 3, 1))
FOUR = THREE + (('d', 1, 0),)


def state_parity(state, mask):
    return (state & mask).bit_count() % 2


class ReviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive = SqliteArchive.open(self.root / 'review.sqlite3')
        self.addCleanup(self.archive.close)
        self.registry = PlanRegistry()

    def publish(self, frames, width=2):
        key = f'sample-{width}'
        plan = self.registry.register(key, width, {})
        path = write_recording(self.root / 'input.jsonl', frames, key=key, width=width)
        return capture_file(self.archive, plan, path)

    def test_joint_values(self):
        first = self.publish(THREE)
        second = self.publish(FOUR)
        replayed = self.publish(THREE + (THREE[0],))
        self.assertEqual(query(self.archive, first, 1, 2), [(0, 0), (0, 1), (1, 0)])
        self.assertEqual(query(self.archive, second, 1, 2), [(0, 0), (0, 1)])
        self.assertEqual(query(self.archive, replayed, 1, 2), [(0, 0), (0, 1), (1, 0)])
        self.assertEqual(query(self.archive, second, 1), [(0,)])

    def test_same_state(self):
        capture_id = self.publish(THREE)
        self.assertEqual(query(self.archive, capture_id, 1), [(0,), (1,)])
        self.assertEqual(query(self.archive, capture_id, 2), [(0,), (1,)])
        joint = query(self.archive, capture_id, 1, 2)
        self.assertEqual(joint, [(0, 0), (0, 1), (1, 0)])
        self.assertNotIn((1, 1), joint)

    def test_single_values(self):
        capture_id = self.publish(THREE)
        self.assertEqual(query(self.archive, capture_id, 2), [(0,), (1,)])

    def test_query_order(self):
        capture_id = self.publish(FOUR)
        self.assertEqual(query(self.archive, capture_id, 1, 2), [(0, 0), (0, 1)])
        self.assertEqual(query(self.archive, capture_id, 2, 1), [(0, 0), (1, 0)])

    def test_repeated_mask(self):
        capture_id = self.publish(THREE)
        self.assertEqual(query(self.archive, capture_id, 2, 2), [(0, 0), (1, 1)])

    def test_mask_limits(self):
        high = 1 << 63
        capture_id = self.publish((('a', high, 1), ('b', high, 1)), width=64)
        self.assertEqual(query(self.archive, capture_id, high), [(1,)])
        self.assertEqual(query(self.archive, capture_id, high, 1), [(1, 0), (1, 1)])
        invalid = ((), (1, 2, 3), (0,), (-1,), (1 << 64,), (1.0,), (True,), (None,), (1, 1 << 64))
        for masks in invalid:
            with self.subTest(masks=masks):
                with self.assertRaises(QueryParameterError):
                    query(self.archive, capture_id, *masks)
        small = self.publish(THREE)
        with self.assertRaises(QueryParameterError):
            query(self.archive, small, high)

    def test_permutation(self):
        for frames, expected in (
            (THREE, [(0, 0), (0, 1), (1, 0)]),
            (FOUR, [(0, 0), (0, 1)]),
        ):
            for order in permutations(frames):
                with self.subTest(order=order):
                    capture_id = self.publish(order)
                    self.assertEqual(query(self.archive, capture_id, 1, 2), expected)
                    self.assertEqual(
                        tuple(frame.frame_id for frame in self.archive.get(capture_id).evidence.frames),
                        tuple(frame_id for frame_id, _, _ in order),
                    )

    def test_empty_evidence(self):
        capture_id = self.publish(())
        self.assertEqual(self.archive.get(capture_id).evidence.frames, ())
        self.assertEqual(query(self.archive, capture_id, 1, 2), [(0, 0), (0, 1), (1, 0), (1, 1)])

    def test_small_width_oracle(self):
        for width, frame_masks in ((1, (1, 1, 1, 1)), (2, (1, 2, 3, 1)), (3, (1, 2, 3, 4, 7))):
            legal_masks = range(1, 1 << width)
            requests = [(mask,) for mask in legal_masks] + list(product(legal_masks, repeat=2))
            for values in product((0, 1), repeat=len(frame_masks)):
                with self.subTest(width=width, frame_masks=frame_masks, values=values):
                    frames = tuple((str(i), mask, values[i]) for i, mask in enumerate(frame_masks))
                    consistent_states = [
                        state for state in range(1 << width)
                        if sum(state_parity(state, mask) != value for _, mask, value in frames) <= 1
                    ]
                    if not consistent_states:
                        with self.assertRaises(CaptureRejected) as caught:
                            self.publish(frames, width=width)
                        self.assertIsInstance(caught.exception.__cause__, NoExplanation)
                        continue
                    capture_id = self.publish(frames, width=width)
                    for masks in requests:
                        expected = sorted({
                            tuple(state_parity(state, mask) for mask in masks) for state in consistent_states
                        })
                        self.assertEqual(query(self.archive, capture_id, *masks), expected, masks)
