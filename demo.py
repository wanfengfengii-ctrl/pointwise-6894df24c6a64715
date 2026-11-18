import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.collect import CaptureRejected, capture
from archive.storage import SqliteArchive
from plans.registry import PlanRegistry
from recordings.batches import read_recording
from recordings.source import RecordingSource
from review.export import export, export_rejection
from review.query import query
from review.verification import verify_export, verify_rejection


def require_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f'{label} 预期 {expected!r}，实际 {actual!r}')


def main():
    samples = Path(__file__).resolve().parent / 'samples'
    plan = PlanRegistry().register('station-12', 2, {'x': 1, 'y': 2})
    masks = (plan.catalog.get_mask('x'), plan.catalog.get_mask('y'))
    filenames = ('normal.jsonl', 'survey.jsonl', 'recheck.jsonl')
    expected = ([(0, 1)], [(0, 0), (0, 1), (1, 0)], [(0, 0), (0, 1)])

    proof_plan = PlanRegistry().register('station-proof', 3, {'x': 1, 'y': 2, 'z': 4})

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

            # 无解录制：完整接纳后在全批至多一次失配下无解，留存诊断并给出诊断号。
            with RecordingSource(samples / 'rejection.jsonl') as source:
                try:
                    capture(archive, proof_plan, read_recording(source))
                except CaptureRejected as exc:
                    require_equal(isinstance(exc.diagnostic_id, str) and bool(exc.diagnostic_id), True, '诊断号')
                    diagnostic_id = exc.diagnostic_id
                else:
                    raise AssertionError('无解录制未被拒绝')
            sealed_frames = [
                [frame.line, frame.frame_id, frame.mask, frame.value]
                for frame in archive.get_diagnostic(diagnostic_id).evidence.frames
            ]
            first_certificate = export_rejection(archive, diagnostic_id)

        # 关闭录制源与归档，移走原录制后重开，仅凭诊断号导出并复核。
        with SqliteArchive.open(database) as reopened:
            offline_records = []
            for capture_id, expected_values, original in zip(ids, expected, reports, strict=True):
                current = export(reopened, capture_id, *masks)
                check = verify_export(reopened, original, capture_id, *masks)
                require_equal(query(reopened, capture_id, *masks), expected_values, '离线查询')
                require_equal(current, original, '离线导出')
                require_equal(check.matches, True, '离线复核')
                offline_records.append(current.as_dict())
            certificate = export_rejection(reopened, diagnostic_id)
            require_equal(certificate, first_certificate, '重开诊断导出')
            require_equal(verify_rejection(reopened, certificate, diagnostic_id), True, '重开矛盾证书复核')
            require_equal(verify_rejection(reopened, certificate, diagnostic_id + '-wrong'), False, '错号复核')

        scenario = {
            'case_id': 'v8',
            'input': {'recordings': filenames, 'masks': masks, 'accepted_frame_ids': accepted_ids},
            'expected': expected,
            'actual': actual,
            'downstream_expected': expected,
            'downstream_actual': [record['combinations'] for record in offline_records],
            'rejection': {
                'recording': 'rejection.jsonl',
                'plan': {'key': 'station-proof', 'bit_width': 3, 'loops': [['x', 1], ['y', 2], ['z', 4]]},
                'sealed_frames': sealed_frames,
                'diagnostic_id': diagnostic_id,
                'certificate': certificate,
                'recheck': True,
            },
            'code_paths': [
                'recordings/batches.py', 'admission/frames.py', 'archive/storage.py',
                'archive/feasibility.py', 'review/projection.py', 'review/export.py', 'review/verification.py',
                'archive/collect.py', 'archive/diagnostics.py', 'archive/certificate.py',
            ],
            'oracle': '手算二位状态 00、01、10、11，逐物理帧计失配，最多保留一次；无解批按帧子集给出各组异或见证。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False))
        for record in offline_records:
            print(json.dumps(record, ensure_ascii=False))
        print(json.dumps({'diagnostic_id': diagnostic_id, 'groups': certificate['groups']}, ensure_ascii=False))
        print('POINTWISE_REJECTION ' + json.dumps({
            'diagnostic_id': diagnostic_id,
            'certificate': certificate,
            'verified': True,
        }, ensure_ascii=False))


if __name__ == '__main__':
    main()
