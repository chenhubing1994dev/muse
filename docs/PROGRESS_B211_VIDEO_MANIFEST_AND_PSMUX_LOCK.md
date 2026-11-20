# B211｜视频产物 Manifest 哈希账本与 psmux 锁恢复

日期：2026-11-20

## 本阶段完成

### 视频产物账本

- initiative_video_attempts 新增 manifest_sha256；新建数据库直接包含该列，旧数据库通过 additive migration 补列，既有任务行保留并以 NULL 表示历史记录没有 Manifest 哈希。
- InitiativeStore.complete_video_attempt() 校验可选 Manifest SHA-256 格式；成功记录持久化 Manifest 哈希，成功事件 details_json 同时记录该哈希。
- Manifest 哈希参与成功状态的幂等比较；同一 attempt 以不同 Manifest 哈希重放会被拒绝。
- VideoTaskWorker 现在要求 Builder 同时返回 Manifest、MP4、ZIP 三个 64 位小写十六进制 SHA-256；缺失或格式错误时 fail-closed，不把 attempt 标为 succeeded。
- 这一步保存的是 Builder 已验证结果所返回的哈希；Worker 尚未在写账本前再次调用 VideoProductBuilder.verify_path() 独立重验磁盘文件，留作下一阶段。

### psmux 默认会话锁恢复

- 复现 Windows 环境差异：PowerShell 文件流和 Python os.open() 可以打开 muse-dev.lock，但 Python Path.open() 对同一文件触发 PermissionError。
- scripts/webcodex_psmux_exec.py 的锁实现改为 os.open / os.fstat / os.lseek，使用原生文件描述符调用 Windows msvcrt.locking 或 POSIX fcntl.flock；释放锁时兼容文件描述符。
- 没有修改 ACL、删除锁文件、切换 Session 绕过，也没有更改 Runner 配置或任务计划。
- 默认 muse-dev 会话已成功执行 detached psmux 测试任务。

## 验证证据

| 验证项 | 结果 |
|---|---:|
| tests/test_webcodex_psmux_exec.py | **30/30 passed** |
| tests/test_muse_dev_cli.py | **8/8 passed** |
| tests/test_video_attempt_ledger.py | **8/8 passed** |
| 项目全量 Python 3.11 unittest discovery | **1,697 tests，OK，9 skipped，0 failures / 0 errors** |

- 全量回归由本轮 `py -3.11 scripts/muse_dev.py run` detached psmux Job 完成；`tests/test_video_task_worker.py` 另行专项 **13/13 passed**。
- 没有启动 Wan/ComfyUI GPU 生成；本阶段的验证不代表新生成真实影片，也不代表 MP4 已经人工播放验收。
## 下一步

1. Worker 收到 Builder 结果后，按 initiative_id / version_id 再调用 VideoProductBuilder.verify_path()。
2. 比较重验得到的 Manifest、MP4、ZIP 哈希与 Builder 返回值；任何版本错配、文件变更或哈希差异都必须阻止成功入账。
3. 验证 product.zip 解包内容、MP4/音轨可解码与实际播放，再将真实验收状态写入产品记录。
4. 继续保持内容审核与公开发布分离；Worker 成功不等于自动发布。
