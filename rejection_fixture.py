import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from admission.capture import collect_evidence
from archive.collect import CaptureRejected, capture
from archive.storage import SqliteArchive
from plans.registry import PlanRegistry
from recordings.batches import read_recording
from recordings.source import RecordingSource
from review.export import export_rejection


@contextmanager
def deny_sqlite_commit(connection):
    # 拒绝此测试连接上的 SQLite COMMIT，并记录实际拒绝的动作。
    denied = []

    def authorize(action, operation, _arg, _database, _origin):
        if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
            denied.append(operation)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    try:
        yield denied
    finally:
        connection.set_authorizer(None)


def check_recording():
    path = Path(__file__).resolve().parent / "samples" / "rejection.jsonl"
    plan = PlanRegistry().register("station-proof", 3, {"x": 1, "y": 2, "z": 4})
    with RecordingSource(path) as source:
        frames = collect_evidence(plan, read_recording(source)).frames
    observed = [(frame.line, frame.frame_id, frame.mask, frame.value) for frame in frames]
    expected = [(2, "a", 1, 0), (3, "b", 2, 0), (4, "c", 3, 1),
                (5, "d", 4, 0), (6, "e", 6, 1), (7, "f", 5, 1)]
    if observed != expected:
        raise AssertionError(f"样例物理帧与约定不符：{observed!r}")
    distances = [
        sum((state & frame.mask).bit_count() % 2 != frame.value for frame in frames)
        for state in range(1 << plan.bit_width)
    ]
    if min(distances) != 2:
        raise AssertionError(f"样例失配数与预期不符：{distances!r}")


def check_commit_control():
    with closing(sqlite3.connect(":memory:", isolation_level=None, cached_statements=0)) as connection:
        connection.execute("CREATE TABLE observations (value INTEGER)")
        connection.execute("INSERT INTO observations VALUES (1)")
        with deny_sqlite_commit(connection) as denied:
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("INSERT INTO observations VALUES (2)")
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError("测试连接未拒绝提交")
        rows = connection.execute("SELECT value FROM observations").fetchall()
        if denied != ["COMMIT"] or connection.in_transaction or rows != [(1,)]:
            raise AssertionError(f"提交控制未保全原行：{denied!r}, {rows!r}")


def check_rejection_capture():
    path = Path(__file__).resolve().parent / "samples" / "rejection.jsonl"
    plan = PlanRegistry().register("station-proof", 3, {"x": 1, "y": 2, "z": 4})
    with TemporaryDirectory() as directory:
        database = Path(directory) / "diagnostics.sqlite3"
        with SqliteArchive.open(database) as archive:
            with closing(sqlite3.connect(database, isolation_level=None)) as connection:
                wrapped = SqliteArchive(connection)
                with deny_sqlite_commit(connection) as denied:
                    with RecordingSource(path) as source:
                        try:
                            capture(wrapped, plan, read_recording(source))
                        except CaptureRejected as exc:
                            if exc.diagnostic_id is not None:
                                raise AssertionError("诊断提交被拒绝时不应给出诊断号")
                        else:
                            raise AssertionError("样例应当被拒绝")
                if denied != ["COMMIT"]:
                    raise AssertionError(f"检查未命中诊断提交路径：{denied!r}")
                if connection.in_transaction:
                    raise AssertionError("拒绝提交后连接仍有未提交事务")
            with RecordingSource(path) as source:
                try:
                    capture(archive, plan, read_recording(source))
                except CaptureRejected as exc:
                    diagnostic_id = exc.diagnostic_id
                else:
                    raise AssertionError("样例应当被拒绝")
            if not isinstance(diagnostic_id, str) or not diagnostic_id:
                raise AssertionError(f"无解拒绝未给出诊断号：{diagnostic_id!r}")
            certificate = export_rejection(archive, diagnostic_id)
            if certificate.get("diagnostic_id") != diagnostic_id:
                raise AssertionError("导出诊断号与提交不符")
            if certificate.get("groups") != [
                ["a", "b", "c"], ["b", "d", "e"], ["a", "d", "f"],
            ]:
                raise AssertionError(f"样例证书与手算不符：{certificate!r}")


if __name__ == "__main__":
    check_recording()
    check_commit_control()
    check_rejection_capture()
    print("录制夹具、SQLite 提交控制与诊断提交校验通过")
