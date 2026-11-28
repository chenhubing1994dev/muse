
## KI-001 代理无法在本机执行命令（2026-11-27 22:4x）
- 现象：
  - 设备端 Python：`ImportError: Sandbox: import of 'subprocess' is blocked`；`PermissionError: Sandbox: os.system() is blocked — shell/process execution not allowed`；`sys.path`/`importlib` 同样被禁止，无法在进程内加载 `muse` 包。
  - 宿主“打开文件”工具：`RuntimeError - Cannot open executable files.`（`run_diag_auto.bat`）。
  - 云端 Python 沙箱：禁止 `importlib`、`types`、`sys.path`，且无法访问本机 FFmpeg/node/npm。
- 根因：执行权限由宿主工具层限制，不是 Muse 代码问题。
- 影响：T1–T10 均要求“自己运行诊断并以磁盘结果为准”，全部无法闭环。
- 可选解法（需人工一次性操作）：双击 `start_diag_watcher.bat` 保持窗口开启；之后代理写 `reports/diag_request.flag` 即可触发诊断并读取结果。

## KI-001 状态更新（2026-11-27 22:41）
- 用户已启动诊断监听器；此后全部诊断经 `reports/diag_request.flag` 触发并成功读取结果。KI-001 已解决（通过人工一次性启用的通道，非绕过）。

## KI-002 编辑通道同步延迟导致一次诊断运行了旧版本探针（23:04）
- 现象：L3.6 已写入文件，但该次运行结果中 AC-015 为 NOT_RUN。
- 处理：触发前先回读确认探针存在并等待数秒；重跑后通过。

## KI-003 迁移报告同秒覆盖（23:08，已修复）
- 根因：报告文件名只精确到秒，同一秒生成的多份报告互相覆盖；L3.6 回归捕获。修复：文件名加随机后缀。

## KI-004 L5.M1 无法执行（环境）
- 诊断进程在 PATH、System32、NVSMI、DriverStore 中均未找到 nvidia-smi；LM Studio 当前也未运行。按规则 SKIP，不计入通过。

## KI-005 pytest 旧用例可能需要更新（未在本轮运行）
- T2 改变了进化接口在审批前的响应（task_id 可能为 null），T5 给所有 JSON 响应增加顶层标签字段，T6 让无票据的 Ruflo 执行调用被拒。依赖精确响应体或直接调用真实 RufloMCPAdapter 的既有 pytest 用例可能失败；本轮只运行了 muse_diag，未运行 3,048 条 pytest。

## KI-005 已解决（2026-11-27 23:52）
- 全量单测首次基线：2,884 ran / 5 failures / 18 errors → 修复后 3,125 ran / 0 / 0（1 skip）。分类与根因见 reports/cycle_log.md C1。
## KI-006 单测阶段耗时约 260s，每周期全量诊断总耗时约 5 分钟（可接受；性能待办 G 跟进）。

## KI-007 file_edit 的 diff 若以“半行”作上下文，会把该行截断（C10 截断了 L7.4 装饰器，ast 校验当场发现并修复）。规则：diff 上下文必须是完整行；每次编辑后在设备端 ast.parse 并回读。

## KI-008 同一文件并行发起两次 diff 编辑，后一次覆盖前一次（C13 10:36）
- 现象：diag_unit_stage.py 两个编辑同时提交，RING_TABLES 定义丢失，而 ast 校验仍通过（只是未定义名）。已触发的诊断作废重跑。
- 规则：同一文件的编辑必须串行；编辑后除 ast.parse 外，还要对每个新增标识符计数核对（定义 + 使用）。

## KI-009 C21 编辑 tests/test_memory_candidates.py 前漏做备份（流程失误，11:5x）
- 影响：该文件无周期备份；原 2 行已逐字记入 backups/cycle-C21-*/NOTE.txt 与 D-033，可手工恢复。
- 规则：修复测试失败而需追加改动文件时，先复制到本周期备份目录再编辑。
