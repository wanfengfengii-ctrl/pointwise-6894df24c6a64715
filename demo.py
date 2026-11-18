import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.collect import capture
from archive.storage import SqliteArchive
from plans.registry import PlanRegistry
from recordings.batches import read_recording
from recordings.source import RecordingSource
from review.export import export
from review.query import query
from review.verification import verify_export


def require_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f'{label} 预期 {expected!r}，实际 {actual!r}')


def main():
    samples = Path(__file__).resolve().parent / 'samples'
    plan = PlanRegistry().register('station-12', 2, {'x': 1, 'y': 2})
    masks = (plan.catalog.get_mask('x'), plan.catalog.get_mask('y'))
    filenames = ('normal.jsonl', 'survey.jsonl', 'recheck.jsonl')
    expected = ([(0, 1)], [(0, 0), (0, 1), (1, 0)], [(0, 0), (0, 1)])

    with TemporaryDirectory() as directory:
        database = Path(directory) / 'shift.sqlite3'
        with SqliteArchive.open(database) as archive:
            ids = []
            reports = []
            actual = []
            accepted_ids = []
            for filename, expected_values in zip(filenames, expected, strict=True):
                with RecordingSource(samples / filename) as source:
                    capture_id = capture(archive, plan, read_recording(source))
                values = query(archive, capture_id, *masks)
                require_equal(values, expected_values, filename)
                ids.append(capture_id)
                actual.append(values)
                accepted_ids.append([frame.frame_id for frame in archive.get(capture_id).evidence.frames])
                reports.append(export(archive, capture_id, *masks))

        with SqliteArchive.open(database) as reopened:
            offline_records = []
            for capture_id, expected_values, original in zip(ids, expected, reports, strict=True):
                current = export(reopened, capture_id, *masks)
                check = verify_export(reopened, original, capture_id, *masks)
                require_equal(query(reopened, capture_id, *masks), expected_values, '离线查询')
                require_equal(current, original, '离线导出')
                require_equal(check.matches, True, '离线复核')
                offline_records.append(current.as_dict())

        scenario = {
            'case_id': 'v8',
            'input': {'recordings': filenames, 'masks': masks, 'accepted_frame_ids': accepted_ids},
            'expected': expected,
            'actual': actual,
            'downstream_expected': expected,
            'downstream_actual': [record['combinations'] for record in offline_records],
            'code_paths': [
                'recordings/batches.py', 'admission/frames.py', 'archive/storage.py',
                'archive/feasibility.py', 'review/projection.py', 'review/export.py', 'review/verification.py',
            ],
            'oracle': '手算二位状态 00、01、10、11，逐物理帧计失配，最多保留一次。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False))
        for record in offline_records:
            print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    main()
