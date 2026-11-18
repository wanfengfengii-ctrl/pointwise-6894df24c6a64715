# 水站采样录制归档

巡检仪离线录制采样，值班员登记对应的位布局，采集员提交完整录制。质控员从成果中查单回路或两路共同可能的读值，据此安排复测。复核员凭已交付成果 ID 和查询顺序，在下一班重开归档复核。

## 运行

使用 Python 3.11 标准库。在项目根目录执行完整回归与业务样例。

```sh
python3 -B -S -m unittest discover -s tests -v
python3 -B -S demo.py
```

样例从 `samples/` 读取巡检录制，在临时目录建立归档。普通录制确定了 x=0、y=1。初次巡检只能确认联合值为 00、01、10。独立复测多取得一条 x=0，成果的联合值缩小为 00、01。演示关闭源和归档后，以原成果 ID 复核交接。

## 录制和计证口径

源为单批 UTF-8 JSON Lines，每行一个事件。文件必须以开始事件开头，以显式结束事件结尾。帧可为空。字段名区分大小写，额外 JSON 字段可作为源元数据，行内重复键属于错误。

```jsonl
{"type":"start","key":"station-12","bit_width":2}
{"type":"frame","frame_id":"a","mask":1,"value":0}
{"type":"frame","frame_id":"b","mask":2,"value":0}
{"type":"frame","frame_id":"c","mask":3,"value":1}
{"type":"end"}
```

采样键、帧 ID、回路标签使用非空字符串。位宽为 1～64，掩码从最低位起编号，取值范围是 1 到 2 的位宽次方减一。读值是掩码选中各位的二元异或。数值字段要求整数，布尔值、浮点值和 null 均无效。空行、截断、越界帧、重复开始及结束后多出的行属于录制错误。读取器核对文件末尾后才交付结束事件。

物理帧的作用域是一批采样。同一 ID 的原样重投保留首次位置，改变掩码或读值会拒绝整批。不同 ID 独立计证。一个源状态需在全部独立观测中至多失配一次。发布和查询均采用这一口径。对于有序查询，每组返回值由同一个可解释状态支撑，允许两个相同掩码，组合按字典序排列。计算采用精确整数上的异或消元，帧到达顺序影响追溯位置，联合值集合保持一致。

## Python 接口

```python
from archive.collect import capture
from archive.storage import SqliteArchive
from plans.registry import PlanRegistry
from recordings.batches import read_recording
from recordings.source import RecordingSource
from review.export import export
from review.query import query
from review.verification import verify_export

registry = PlanRegistry()
plan = registry.register('station-12', 2, {'x': 1, 'y': 2})
x, y = plan.catalog.get_mask('x'), plan.catalog.get_mask('y')

with SqliteArchive.open('station.sqlite3') as archive:
    with RecordingSource('samples/survey.jsonl') as source:
        capture_id = capture(archive, plan, read_recording(source))
    possibilities = query(archive, capture_id, x, y)
    record = export(archive, capture_id, x, y)

with SqliteArchive.open('station.sqlite3') as archive:
    result = verify_export(archive, record, capture_id, x, y)
    assert result.matches
```

- `PlanRegistry.register(key, bit_width, loops)` 固定声明，相同内容返回原对象，回路表顺序无关。允许显式传入空字典。`PlanConflict` 表示无效或冲突的声明，`get(key)` 对未登记键报 `KeyError`。登记表随调用者进程管理，已交付成果内嵌原声明。
- `RecordingSource` 是一次性、可显式关闭的源。`read_recording(source)` 产生校验后的事件，`RecordingError.line` 保存已知的源行号。调用者用上下文管理源，可在提前终止时释放文件。
- `capture(archive, plan, events)` 消费整批结束证据，成功时返回已提交的整数成果 ID。声明不匹配、身份冲突、无解释及写失败以 `CaptureRejected` 报告，录制错误使用 `RecordingError`。归档可选用 `before_commit` 回调做提交前检查，回调异常会回滚。回调收到的是事务内临时行号，成功提交后才成为成果 ID。独立读连接在提交前只能见到已提交成果。
- `query(archive, capture_id, *masks)` 返回由整数元组组成的列表。成果不存在报 `CaptureNotFound`，损坏的归档数据报 `ArchiveError`，参数数目或掩码无效报 `QueryParameterError`。掩码按所选成果冻结的位宽校验。
- `export(archive, capture_id, *masks)` 返回不可变交接记录，可通过 `as_dict()` 转为 JSON 可序列化数据。不可交付时报 `ExportError`。`verify_export` 用调用者约定的成果 ID 和掩码重算并比较身份、位宽、参数和联合值，通过 `matches` 与 `mismatches` 返回复核结果。

首次打开空归档建立 SQLite 表。重复打开会检查归档格式并保留既有成果。同一采样键可有多份成功成果，各有自己的 ID。用户录制和本地声明由用户管理，打开归档只操作选定数据库。对固定成果的查询依赖归档内的声明和证据，原录制和登记器可在交付后关闭。
