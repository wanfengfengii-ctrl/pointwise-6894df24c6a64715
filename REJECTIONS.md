# 巡检录制拒绝交接

值班员收到诊断号后可将它交给下一班。即使原录制已移走，复核员仍能从同一个归档确认至少有两条独立物理帧失配。

## 采集与提交

继续调用 `archive.collect.capture(archive, plan, events, *, before_commit=None)`。录制、声明、1～64 位掩码及物理帧身份沿用 `README.md`。保留成功成果整数 ID、原成功发布回调及现有库文件的读取。

仅当录制完整接纳且全批在至多一次读值失配下无解时，才留存拒绝诊断。诊断绑定本次原声明及完整的封存帧，包含各帧的原 ID、首次位置、掩码与读值。每次成功留存的拒绝使用独立的非空字符串诊断号。诊断身份和可离线读取的证据须共同提交。提交后仍抛 `CaptureRejected`，保留无解拒绝原因，并通过异常的 `diagnostic_id` 属性给出该号。其他 `CaptureRejected` 的 `diagnostic_id` 为 `None`。截断或非法录制保留 `RecordingError` 语义。诊断写入或提交失败时继续报告采集拒绝，不给出诊断号，也不得留下已发布的部分诊断。既有成功成果保持可读。`SqliteArchive.publish` 的原直接调用合约保持。

## 导出与复核

`review.export.export_rejection(archive, diagnostic_id) -> dict` 从已提交诊断导出 JSON 可序列化字典，字段为 `diagnostic_id` 和 `groups`。前者为诊断号，后者为非空列表，每项是一个非空物理帧 ID 字符串列表。诊断不存在或证据无效时抛出 `ExportError`。

`review.verification.verify_rejection(archive, record, expected_diagnostic_id) -> bool` 用调用者指定的诊断号读取冻结证据。记录形状、身份及组均通过检查时返回 `True`。错号、缺失诊断、无效记录或坏证据返回 `False`。复核逐组检查以下条件。

- 每组只包含该次冻结的物理帧。同组内 ID 不能重复。
- 每组所引用的帧掩码异或为零，读值异或为一。
- 所有组的总交集为空。允许同一物理帧出现在不同组。任何满足契约的证书均可通过，与导出本身使用的分组及顺序无关。

导出和复核都须识别损坏的冻结证据。证据读取须检查格式与该诊断的绑定。局部损坏使内容换成另一份语法有效的证据时，即使证书仍能通过异或验算，也须拒绝。此处核对归档中固定的内容，范围是记录丢失或损坏。

## 样例与交付

`samples/rejection.jsonl` 使用声明 `PlanRegistry().register('station-proof', 3, {'x': 1, 'y': 2, 'z': 4})`。八种源状态中最少失配两帧。提交该样例须拒绝并给出诊断号。关闭录制源并重开库后，实际导出应能复核。以下是同一个批次的一份手算记录，使用实际诊断号替代示例字符串。

```json
{"diagnostic_id":"example-id","groups":[["a","b","c"],["a","d","f"],["b","d","e"]]}
```

样例的三组两两相交，总交集为空。删掉最后一组后只剩共同帧 `a`，此时证书不通过。另用一位声明录入 `a=0, b=1, c=1`，再重投同 ID 的 `b=1`，录制仍可接纳。另一批增加独立帧 `d=0` 后须拒绝并可复核。对证书检查未知 ID、组内重复、异或不成立、错号和缺失诊断，并核对保存证据损坏后的拒绝。对采集检查截断、同 ID 内容冲突、写入失败及旧成果可读。

`rejection_fixture.py` 提供普通 SQLite 失败控制。可在初始化归档后，用 `SqliteArchive(connection)` 包装测试连接，并在提交录制时用 `deny_sqlite_commit(connection)` 拒绝该连接的 COMMIT。它交付实际拒绝的动作日志，可据此确认检查命中了提交路径。运行 `python3 -B -S rejection_fixture.py` 可检查样例和该控制。这个入口只检查所提供的材料。

保留原回归，补充由完整录制拒绝贯穿到重开复核的联合验证，以及物理帧身份与诊断提交边界说明。更新现有 `.pointwise-runtime.json` 中的 `verify` 和 `demo` 命令。沿用版本 1 的 `cli` 清单，准备命令用 `setup` 中的 argv 列表登记，核验与演示各登记一个 argv。核验包含原回归及新增业务检查，失败须返回非零。演示用所给六帧录制展示实际诊断号、重开后导出的证书和复核结果。
