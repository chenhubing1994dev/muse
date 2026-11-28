# 05 · 四态账本（脚本自动生成，勿手改）

- 生成时间：2026-11-28T12:07:25.683623+08:00（来源：diag_latest 2026-11-28T12:07:25.682114+08:00；单测 2026-11-28T12:07:25.672072+08:00；栅栏 66f3e22364be41078085816d788cae5f）
- 总体：PASS · 探针 57 个 {'PASS': 56, 'FAIL': 0, 'ERROR': 0, 'SKIPPED_ENV': 1}
- 单元测试（unittest (pytest not installed; installs forbidden)）：PASS · ran=3152 · {'failures': 0, 'errors': 0, 'skipped': 1, 'expected_failures': 0, 'unexpected_successes': 0}

| 状态 | 数量（共 76 条 AC） | 编号 |
|---|---:|---|
| 🟢 已运行实测通过 | 75 | AC-001, AC-002, AC-003, AC-004, AC-005, AC-006, AC-007, AC-008, AC-010, AC-011, AC-012, AC-013, AC-014, AC-015, AC-020, AC-021, AC-022, AC-023, AC-024, AC-025, AC-026, AC-027, AC-028, AC-029, AC-031, AC-032, AC-033, AC-034, AC-035, AC-036, AC-037, AC-038, AC-039, AC-040, AC-041, AC-042, AC-043, AC-044, AC-045, AC-046, AC-047, AC-048, AC-049, AC-050, AC-051, AC-052, AC-060, AC-061, AC-070, AC-071, AC-072, AC-073, AC-074, AC-075, AC-076, AC-077, AC-080, AC-081, AC-082, AC-083, AC-084, AC-090, AC-091, AC-092, AC-094, AC-095, AC-096, AC-097, AC-098, AC-099, AC-100, AC-101, AC-102, AC-103, AC-104 |
| 🔴 失败/报错 | 0 | — |
| ⚪ 环境缺失 SKIP（不计通过） | 1 | AC-093 |
| 🟡 未运行 | 0 | — |

## SKIP 原因

- 探针 L5.M1：nvidia-smi not found: GPU memory cannot be measured, refusing to run inference blind
- 单测 test_zhenyi_bazi_provider.ZhenyiBaziProviderTests.test_real_upstream_engine_matches_muse_with_true_solar_time_enabled：optional celestial-calendar==0.8.0 is not installed in this Python environment
