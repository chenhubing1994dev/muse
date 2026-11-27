# CHANGELOG — AWS Quick 修复记录（2026-11-25 起）

> 每条记录对应一次小步修改。原文件备份在 `.qw-backups/20261125/<路径>.orig`，SHA-256 清单见 `.qw-backups/20261125/MANIFEST.txt`。回滚：把 .orig 复制回原路径。
> 用户决定不做整目录备份（空间有限，2026-11-25 16:03），改为逐文件备份。

## [F1-a] src/muse/research_state_machine.py
- 时间：2026-11-25 16:06
- 问题：第 180 行两条赋值语句被拼接在同一行 → SyntaxError，模块与 research_task_handler 无法导入。
- 原因/依据：换行符丢失。仅恢复换行，不改逻辑。（同文件还有第二处语法错误，见 F1-b，因此本步跳过语法校验。）
- SHA-256：`ad7be1ab0425e08e…` → `b5e19b7760c50258…`
- 修改前：

                start_char = citation.get("start_char")            end_char = citation.get("end_char")
    

- 修改后：

                start_char = citation.get("start_char")
                end_char = citation.get("end_char")
    

## [F1-b] src/muse/research_state_machine.py
- 时间：2026-11-25 16:06
- 问题：第 283 行 `elif`（反驳分支）缺少语句体 → SyntaxError；第 285 行同一条 reasons.append 被重复拼接。
- 原因/依据：写入损坏导致一行内容丢失、另一行重复。保守恢复：反驳分支与上面 supported 分支完全对称（≥2 个已验证反驳来源、≥2 个独立域名、无支持来源），按 research_knowledge.CLAIM_STATUSES / review verdict 词表取值 "refuted"；去掉重复的 append。注意：评估结论只写入 research_assessment，claim 状态保持 proposed（claim_status_unchanged=True），不会自动升级事实。该推断无原始版本可对照（文件未被 Git 跟踪），已在 ISSUES.md 标注需人工复核。
- SHA-256：`b5e19b7760c50258…` → `316e5a6731dbb4d0…`
- 修改前：

                elif enough_basis and len(refute_source_refs) >= 2 and len(refute_domains) >= 2 and not support_source_refs:
                elif enough_basis:
                    reasons.append("insufficient_or_conflicting_reviewed_verdicts")                reasons.append("insufficient_or_conflicting_reviewed_verdicts")
    

- 修改后：

                elif enough_basis and len(refute_source_refs) >= 2 and len(refute_domains) >= 2 and not support_source_refs:
                    conclusion = "refuted"
                elif enough_basis:
                    reasons.append("insufficient_or_conflicting_reviewed_verdicts")
    

## [F2] ember-fire/package.py
- 时间：2026-11-25 16:07
- 问题：第 5 行 INCLUDE 列表结尾与 `with` 语句拼接 → SyntaxError，《火种不灭》工程打包脚本无法运行。
- 原因/依据：已提交版本（Git HEAD）中即为损坏状态。仅恢复换行，不改逻辑。
- SHA-256：`f163347f8ccc0748…` → `3c74b6051889a210…`
- 修改前：

    "out/voice_timing.json","stills"]with zipfile.ZipFile(

- 修改后：

    "out/voice_timing.json","stills"]
    with zipfile.ZipFile(

## [I1] src/muse/video_product_builder.py
- 时间：2026-11-25 16:07
- 问题：第 154 行 FFmpeg/FFprobe 子进程以 text=True 捕获输出但未指定编码。
- 原因/依据：中文 Windows 默认按 cp936 解码；FFmpeg 输出为 UTF-8，路径/元数据含非 GBK 字符时会抛 UnicodeDecodeError 中断视频链路。显式 UTF-8 + errors=replace。
- SHA-256：`b6b36bfb004e49be…` → `8040c71a09ec3a9a…`
- 修改前：

             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=False,
    

- 修改后：

             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False,
    

## [I1] src/muse/video_product_builder.py
- 时间：2026-11-25 16:07
- 问题：第 179 行 FFmpeg/FFprobe 子进程以 text=True 捕获输出但未指定编码。
- 原因/依据：中文 Windows 默认按 cp936 解码；FFmpeg 输出为 UTF-8，路径/元数据含非 GBK 字符时会抛 UnicodeDecodeError 中断视频链路。显式 UTF-8 + errors=replace。
- SHA-256：`8040c71a09ec3a9a…` → `c4b62be0eac7a743…`
- 修改前：

             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=90, check=False,
    

- 修改后：

             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
    

## [I1] src/muse/video_product_builder.py
- 时间：2026-11-25 16:07
- 问题：第 1226 行 FFmpeg/FFprobe 子进程以 text=True 捕获输出但未指定编码。
- 原因/依据：中文 Windows 默认按 cp936 解码；FFmpeg 输出为 UTF-8，路径/元数据含非 GBK 字符时会抛 UnicodeDecodeError 中断视频链路。显式 UTF-8 + errors=replace。
- SHA-256：`c4b62be0eac7a743…` → `0d8099d8dba9d213…`
- 修改前：

                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", "-movflags", "+faststart", str(combined)],
                    capture_output=True, text=True, timeout=max(900, timeout_seconds * len(clip_paths)), check=False,
    

- 修改后：

                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", "-movflags", "+faststart", str(combined)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=max(900, timeout_seconds * len(clip_paths)), check=False,
    

## [I1] src/muse/video_product_builder.py
- 时间：2026-11-25 16:07
- 问题：第 1882 行 FFmpeg/FFprobe 子进程以 text=True 捕获输出但未指定编码。
- 原因/依据：中文 Windows 默认按 cp936 解码；FFmpeg 输出为 UTF-8，路径/元数据含非 GBK 字符时会抛 UnicodeDecodeError 中断视频链路。显式 UTF-8 + errors=replace。
- SHA-256：`0d8099d8dba9d213…` → `d28dcbee127244f0…`
- 修改前：

                    mux = subprocess.run(
                        mux_args, capture_output=True, text=True, timeout=900, check=False,
    

- 修改后：

                    mux = subprocess.run(
                        mux_args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, check=False,
    

## [I1] src/muse/video_product_builder.py
- 时间：2026-11-25 16:07
- 问题：第 2230 行 FFmpeg/FFprobe 子进程以 text=True 捕获输出但未指定编码。
- 原因/依据：中文 Windows 默认按 cp936 解码；FFmpeg 输出为 UTF-8，路径/元数据含非 GBK 字符时会抛 UnicodeDecodeError 中断视频链路。显式 UTF-8 + errors=replace。
- SHA-256：`d28dcbee127244f0…` → `d8530e299a885cb1…`
- 修改前：

                     "-of", "json", str(source_video_copy)],
                    capture_output=True, text=True, timeout=60, check=False,
    

- 修改后：

                     "-of", "json", str(source_video_copy)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False,
    

## [I1] src/muse/video_product_builder.py
- 时间：2026-11-25 16:07
- 问题：第 2254 行 FFmpeg/FFprobe 子进程以 text=True 捕获输出但未指定编码。
- 原因/依据：中文 Windows 默认按 cp936 解码；FFmpeg 输出为 UTF-8，路径/元数据含非 GBK 字符时会抛 UnicodeDecodeError 中断视频链路。显式 UTF-8 + errors=replace。
- SHA-256：`d8530e299a885cb1…` → `590dc9eadd3d423f…`
- 修改前：

                     "-movflags", "+faststart", str(output_video)],
                    capture_output=True, text=True, timeout=900, check=False,
    

- 修改后：

                     "-movflags", "+faststart", str(output_video)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, check=False,
    

## [I1] src/muse/video_product_builder.py
- 时间：2026-11-25 16:07
- 问题：第 2263 行 FFmpeg/FFprobe 子进程以 text=True 捕获输出但未指定编码。
- 原因/依据：中文 Windows 默认按 cp936 解码；FFmpeg 输出为 UTF-8，路径/元数据含非 GBK 字符时会抛 UnicodeDecodeError 中断视频链路。显式 UTF-8 + errors=replace。
- SHA-256：`590dc9eadd3d423f…` → `be31d101bb23fca7…`
- 修改前：

                     "-of", "json", str(output_video)],
                    capture_output=True, text=True, timeout=60, check=False,
    

- 修改后：

                     "-of", "json", str(output_video)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False,
    

## [I1] src/muse/model_supervisor.py
- 时间：2026-11-25 16:07
- 问题：schtasks.exe 子进程以 text=True 捕获输出，未处理无法解码的字节。
- 原因/依据：schtasks 输出使用系统代码页（中文 Windows 为 cp936），保持默认编码，但加 errors=replace，避免个别字节导致 UnicodeDecodeError。
- SHA-256：`2c7320ecb9d63d1b…` → `374173ef2df7bd19…`
- 修改前：

            ["schtasks.exe", "/Create", "/SC", "ONLOGON", "/TN", task_name, "/TR", command, "/F"],
            capture_output=True, text=True, timeout=30, check=False,
    

- 修改后：

            ["schtasks.exe", "/Create", "/SC", "ONLOGON", "/TN", task_name, "/TR", command, "/F"],
            capture_output=True, text=True, errors="replace", timeout=30, check=False,
    

## [I1] src/muse/model_supervisor.py
- 时间：2026-11-25 16:07
- 问题：schtasks.exe 子进程以 text=True 捕获输出，未处理无法解码的字节。
- 原因/依据：schtasks 输出使用系统代码页（中文 Windows 为 cp936），保持默认编码，但加 errors=replace，避免个别字节导致 UnicodeDecodeError。
- SHA-256：`374173ef2df7bd19…` → `3ee7302f03de0729…`
- 修改前：

            ["schtasks.exe", "/Delete", "/TN", "MuseLocalModelSupervisor", "/F"],
            capture_output=True, text=True, timeout=30, check=False,
    

- 修改后：

            ["schtasks.exe", "/Delete", "/TN", "MuseLocalModelSupervisor", "/F"],
            capture_output=True, text=True, errors="replace", timeout=30, check=False,
    

## [G1] src/muse/status_api.py
- 时间：2026-11-25 16:08
- 问题：Status API 在浏览器刷新/关闭（WinError 10053/10054）或 5 秒套接字超时时打印完整 traceback，logs/dashboard-task.log 中累计 36 条。
- 原因/依据：local_dashboard.LocalDashboardServer 已有同样的 handle_error，BoundedStatusServer 缺失。照搬该模式并加入 TimeoutError（get_request 设置了 5 秒超时）；其他异常仍走默认处理。
- SHA-256：`aa11ca7be33bcc49…` → `4ecae3d999282939…`
- 修改前：

        def get_request(self) -> tuple[Any, Any]:
            request, client_address = super().get_request()
            request.settimeout(5.0)
            return request, client_address
    

- 修改后：

        def get_request(self) -> tuple[Any, Any]:
            request, client_address = super().get_request()
            request.settimeout(5.0)
            return request, client_address
    
        def handle_error(self, request: Any, client_address: Any) -> None:
            """Treat normal client disconnects/timeouts as quiet, not as server failures."""
            exc = sys.exc_info()[1]
            if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, TimeoutError)):
                return
            super().handle_error(request, client_address)
    

## [I3] README.md
- 时间：2026-11-25 16:08
- 问题：快速开始中 `py -3.11 -m muse.research-wiki-audit`：模块名含连字符，`python -m` 无法导入，命令直接报 No module named。
- 原因/依据：实际模块为 src/muse/research_wiki_audit.py（pyproject 中命令名 muse-research-wiki-audit 指向它）。改为下划线模块名。
- SHA-256：`2d690b15cd087a07…` → `9267b1a83f8a5dca…`
- 修改前：

    py -3.11 -m muse.research-wiki-audit
    py -3.11 -m muse.research-wiki-audit --limit 3
    

- 修改后：

    py -3.11 -m muse.research_wiki_audit
    py -3.11 -m muse.research_wiki_audit --limit 3
    

## [I3] README.md
- 时间：2026-11-25 16:08
- 问题：快速开始的核心自检用 `py -3.14`，其余命令用 `py -3.11`，口径不一致。
- 原因/依据：项目 .venv 为 Python 3.11.9、pyproject 要求 >=3.11，README 第八节的验证基线也是 3.11；3.14 只用于粤语 TTS 隔离环境（.muse-canto-tts-py314）。统一为 3.11。
- SHA-256：`9267b1a83f8a5dca…` → `060978a326d79ef8…`
- 修改前：

    py -3.14 -m unittest discover -s tests -v
    py -3.14 -m muse.main --check
    

- 修改后：

    py -3.11 -m unittest discover -s tests -v
    py -3.11 -m muse.main --check
    

## [I3] README.md
- 时间：2026-11-25 16:08
- 问题：`muse.chat_cli` 示例用 `py -3.14`，与核心环境不一致。
- 原因/依据：同上，统一为 3.11。
- SHA-256：`060978a326d79ef8…` → `f0aceafd79cfccdd…`
- 修改前：

    py -3.14 -m muse.chat_cli "

- 修改后：

    py -3.11 -m muse.chat_cli "

## [I3] README.md
- 时间：2026-11-25 16:08
- 问题：`muse.memory_cli` 示例用 `py -3.14`，与核心环境不一致。
- 原因/依据：同上，统一为 3.11。
- SHA-256：`f0aceafd79cfccdd…` → `7a488fa18188de89…`
- 修改前：

    py -3.14 -m muse.memory_cli add

- 修改后：

    py -3.11 -m muse.memory_cli add

## [I4] pyproject.toml
- 时间：2026-11-25 16:09
- 问题：代码中惰性导入的 faster_whisper、piper、celestial_calendar、ziwei、PIL、llama_cpp 未在 pyproject 声明。
- 原因/依据：补充可选依赖分组 speech / metaphysics / imaging / local-llm，便于按需安装；版本只在代码自身校验精确版本处固定。未安装任何包。canto_tts 仅在 3.14 隔离环境使用，不声明。
- SHA-256：`24c062a8f6c85072…` → `a2798596cb1bf284…`
- 修改前：

    video-diagnostics = [
        "numpy>=1.26",
        "opencv-python-headless>=4.8",
    ]
    

- 修改后：

    video-diagnostics = [
        "numpy>=1.26",
        "opencv-python-headless>=4.8",
    ]
    # Optional groups below were added 2026-11-25 to declare imports that the code
    # already uses lazily (each module fails closed when the package is missing).
    # Versions pinned only where the code itself checks an exact version.
    speech = [
        "faster-whisper",  # muse.cantonese_asr
        "piper-tts",       # muse.piper_tts
    ]
    metaphysics = [
        "celestial-calendar==0.8.0",  # muse.celestial_calendar_provider
        "ziwei==0.4.1",               # muse.ziwei_adapter
    ]
    imaging = [
        "Pillow>=10",  # muse.kinetic_explainer, muse.smart_crop
    ]
    local-llm = [
        "llama-cpp-python",  # muse.gemma_intent
    ]
    # canto_tts (muse.cantonese_tts) runs only in the isolated Python 3.14 env
    # .muse-canto-tts-py314 and is intentionally not declared here.
    

## [I2] docs/PROGRESS.md
- 时间：2026-11-25 16:10
- 问题：695 行是 UTF-8 文本被按 GBK/cp936 误解码后再以 UTF-8 回存的乱码（如“鎸佺画杩涘害璁板綍”= “持续进度记录”）。
- 原因/依据：逐行检测乱码特征字比例 >12% 的行，按 cp936（gb18030 + € 映射为 0x80）重新编码后再按 UTF-8 解码。原损坏时每个被截断的字符已被替换成“?”，原始字节丢失，无法恢复，共 2727 处，统一标记为“�”而不猜测。其余行、换行符（CRLF/LF 原样）与 BOM 均未改动。
- 涉及行：7, 17, 22, 27, 31, 35–36, 42–43, 46, 48, 52, 54–57, 60, 63, 68, 71, 74–75, 80, 82, 86, 90, 96, 101, 103–105, 107, 110, 112, 115, 119–120, 123–124, 128, 131, 134, 136, 141, 143, 150, 156, 160–161, 163–164, 168–169, 172–174, 176–177, 179, 181–182, 185–186, 190, 192–196, 199, 204, 206–207, 212, 214, 217, 219, 221–222, 227, 230, 237, 239–240, 242–243, 245–246, 249, 251, 253–254, 257–264, 268, 271–273, 276, 278, 280, 283, 285–288, 290–294, 297–301, 303, 305–310, 315–319, 322–327, 329, 331–339, 341–347, 349, 352, 354, 357–361, 364–365, 367–368, 372–373, 375, 378, 380–382, 384, 387–388, 390–391, 396–397, 400–404, 407, 415–416, 418–419, 421, 426, 428–431, 433–434, 436, 438, 442–443, 446–448, 450, 452, 455–456, 458–459, 462, 464, 467–468, 473–474, 476, 478, 480, 485–487, 489–490, 492–494, 500, 503–504, 510, 512–513, 515, 517–518, 522, 526, 528, 532, 534–535, 540, 543, 549, 553, 556, 561–562, 565–567, 569–571, 574–578, 580–582, 585, 587–588, 591–592, 594, 597–598, 600–601, 603–604, 607, 612, 615–616, 620, 623, 625–627, 630–631, 634, 637, 641, 643, 645–651, 653–657, 659, 667–669, 671, 674–675, 682, 686–688, 692, 695, 701, 703, 707–708, 711, 714, 717–718, 729, 732, 736, 738–739, 742–744, 746–747, 749–750, 753–755, 758, 761, 764, 769–770, 772–773, 779, 781, 783–785, 787–788, 790–792, 794, 796, 798–799, 801–803, 805–808, 814, 816–817, 823–825, 827, 829–831, 834, 839, 843–848, 852, 856–857, 859–861, 863–865, 867, 870–872, 875, 877–881, 885–888, 892–894, 896, 901, 904–905, 907–908, 910–916, 924–926, 928–929, 946, 953–954, 962, 966, 971, 976, 979–980, 984–985, 988, 991, 993–995, 997–998, 1001–1003, 1005, 1007, 1011, 1013, 1015, 1028–1029, 1034, 1048, 1053–1054, 1057, 1061–1062, 1070, 1074, 1080, 1091, 1110, 1127–1128, 1136, 1145–1147, 1153, 1158–1161, 1168, 1174, 1177, 1187–1188, 1196–1197, 1199, 1205–1206, 1209, 1212–1215, 1219, 1223, 1226, 1229–1230, 1232–1234, 1238–1239, 1243–1244, 1251–1252, 1258, 1267–1269, 1277–1278, 1282–1283, 1288, 1291, 1296, 1300, 1303–1304, 1308, 1311–1312, 1316–1318, 1322, 1324, 1326–1327, 1337–1338, 1344, 1347, 1349, 1354, 1360, 1365, 1369, 1371–1373, 1384, 1386–1388, 1390, 1393, 1403–1406, 1408, 1412, 1414–1418, 1435, 1438–1439, 1444, 1446–1447, 1453, 1455–1458, 1460–1461, 1467, 1470, 1472, 1474–1475, 1477–1478, 1480, 1484, 1486–1487, 1489, 1491–1492, 1496–1497, 1499–1500, 1502–1503, 1506, 1510, 1512–1515, 1517, 1520, 1524–1526, 1528–1532, 1534, 1540–1543, 1545, 1552–1553, 1556, 1562–1564, 1572, 1574–1575, 1593–1595, 1597, 1603, 1606–1607, 1612, 1616, 1620, 1625, 1631–1635, 1640–1645, 1649, 1652–1654, 1656–1660, 1662–1663, 1666, 1670–1671, 1673–1675, 1678, 1683–1685, 1687–1688, 1692, 1695–1697, 1699, 1704, 1724, 1726, 1729, 1735–1736, 1738, 1748, 1752, 1760, 1765, 1767, 1770, 1772, 1774, 1777
- SHA-256：`4fd30abf015de543…` → `cb0234039d577bc5…`（原文件完整备份：`.qw-backups/20261125/docs/PROGRESS.md.orig`）
- 示例（第 17 行）：

    修改前：1. 涓诲姩浠庡垱閫犺€呬功绛惧拰鍏磋叮鍙戠幇鏂版柟鍚戙€?
    修改后：1. 主动从创造者书签和兴趣发现新方向�

## [I7] src/muse/research_state_machine.py
- 时间：2026-11-25 16:10
- 问题：第 131 行 if 与 raise 拼在同一行。
- 原因/依据：与 F1 同一次写入损坏（该文件今天新建、未入 Git）。语法合法、语义不变，仅恢复换行以免后续编辑误判缩进。
- SHA-256：`316e5a6731dbb4d0…` → `5d9ef560cf2ea8ef…`
- 修改前：

                if cursor.rowcount != 1:                raise ResearchStateError("research metadata compare-and-swap failed")
    

- 修改后：

                if cursor.rowcount != 1:
                    raise ResearchStateError("research metadata compare-and-swap failed")
    

## [I7] src/muse/research_state_machine.py
- 时间：2026-11-25 16:10
- 问题：第 185 行 if 与 continue 拼在同一行。
- 原因/依据：与 F1 同一次写入损坏（该文件今天新建、未入 Git）。语法合法、语义不变，仅恢复换行以免后续编辑误判缩进。
- SHA-256：`5d9ef560cf2ea8ef…` → `7db554d6c86abc94…`
- 修改前：

                if not _valid_sha(citation.get("excerpt_sha256")):                 continue
    

- 修改后：

                if not _valid_sha(citation.get("excerpt_sha256")):
                    continue
    

## [I7] src/muse/research_state_machine.py
- 时间：2026-11-25 16:10
- 问题：第 313 行两个字典键拼在同一行。
- 原因/依据：与 F1 同一次写入损坏（该文件今天新建、未入 Git）。语法合法、语义不变，仅恢复换行以免后续编辑误判缩进。
- SHA-256：`7db554d6c86abc94…` → `241c9548df438084…`
- 修改前：

                    "automatic_fact_promotion": False,                "requires_human_confirmation_before_fact_promotion": True,
    

- 修改后：

                    "automatic_fact_promotion": False,
                    "requires_human_confirmation_before_fact_promotion": True,
    

## [P2-README] README.md
- 时间：2026-11-25 16:24
- 问题：README 缺少一键验证主流程的入口、依赖/环境变量说明与常见问题。
- 原因/依据：新增冒烟测试命令、requirements / .env.example 说明与 4 条常见问题；插入在“日常 Windows 使用”之前，不改动原有内容。
- SHA-256：`7a488fa18188de89…` → `a3b563d591e9f410…`
- 修改前：

    **日常 Windows 使用（无需 ChatGPT）**

- 修改后：

    **一键冒烟测试（推荐先跑）**：不联网、不需要模型/GPU，在临时目录中验证 编译 → 全模块导入 → 配置检查 → 任务入队/审批/执行 → 灵感生成离线产品包 → 研究状态机 → Status API，结果写入 `reports\qw_smoke\latest.log` 与 `latest.json`。
    ```powershell
    cd "<MUSE_ROOT>"
    & ".\.venv\Scripts\python.exe" "scripts\muse_smoke.py"          # 冒烟 + 重点回归测试
    & ".\.venv\Scripts\python.exe" "scripts\muse_smoke.py" --full   # 冒烟 + 全量 unittest
    ```
    
    **依赖与配置**：核心只需 Python 3.11 标准库（`requirements.txt` 仅含 Windows 时区数据 `tzdata`）；语音、玄学引擎、图像、本地 LLM 等可选能力见 `requirements-optional.txt` 或 `pip install -e ".[speech]"` 等分组。复制 `config\muse.example.toml` 为 `config\muse.toml` 进行本机配置；可选环境变量（本地服务地址、工具路径、Portal 密码、云端 Key）见 `.env.example`，Muse 只从进程环境变量读取，不自动加载 .env，真实密钥切勿入库。
    
    **常见问题**
    - `No module named muse`：先执行 `$env:PYTHONPATH = 'src'`，或在项目根目录运行 `pip install -e .`。
    - 中文路径/输出乱码：使用 PowerShell 7 或先执行 `$env:PYTHONUTF8 = '1'`；冒烟脚本日志始终为 UTF-8。
    - 某模块提示 needs optional package：属可选能力，按 `requirements-optional.txt` 安装对应分组即可，核心流程不受影响。
    - 路径含空格：所有命令中的 `<MUSE_ROOT>` 必须加双引号。
    
    **日常 Windows 使用（无需 ChatGPT）**

## [P1-CTX] src/muse/assistant.py
- 时间：2026-11-25 17:03
- 问题：MuseAssistant 每轮把整条线程的全部历史发给模型，长线程上下文与 Token 无上限增长（无短期记忆滑窗）。
- 原因/依据：新增构造参数 context_max_messages=40 / context_max_chars=60000（带校验）；仅影响发给模型的上下文，持久化的完整历史不变（保持可审计）。
- SHA-256：`df71e149c16c9f82…` → `e375fa3831e806a4…`
- 修改前：

            memory_limit: int = 5,
            memory_candidate_extractor: MemoryCandidateExtractor | None = None,
        ) -> None:
            if memory_limit < 1:
                raise ValueError("memory_limit must be at least 1")
    

- 修改后：

            memory_limit: int = 5,
            memory_candidate_extractor: MemoryCandidateExtractor | None = None,
            context_max_messages: int = 40,
            context_max_chars: int = 60_000,
        ) -> None:
            if memory_limit < 1:
                raise ValueError("memory_limit must be at least 1")
            if isinstance(context_max_messages, bool) or not isinstance(context_max_messages, int) or context_max_messages < 2:
                raise ValueError("context_max_messages must be an integer of at least 2")
            if isinstance(context_max_chars, bool) or not isinstance(context_max_chars, int) or context_max_chars < 1_000:
                raise ValueError("context_max_chars must be an integer of at least 1000")
            self.context_max_messages = context_max_messages
            self.context_max_chars = context_max_chars
    

## [P1-CTX] src/muse/assistant.py
- 时间：2026-11-25 17:03
- 问题：同上：组装模型上下文时直接复制全部历史。
- 原因/依据：改为调用 _context_window() 取有界窗口。
- SHA-256：`e375fa3831e806a4…` → `37219c2adc08a4c7…`
- 修改前：

            checkpoint = self.conversations.load(thread_id)
            stored_messages = [] if checkpoint is None else list(checkpoint["messages"])
            model_messages = list(stored_messages)
    

- 修改后：

            checkpoint = self.conversations.load(thread_id)
            stored_messages = [] if checkpoint is None else list(checkpoint["messages"])
            model_messages = self._context_window(stored_messages)
    

## [P1-CTX] src/muse/assistant.py
- 时间：2026-11-25 17:03
- 问题：同上：缺少滑窗实现。
- 原因/依据：保留开头 system 消息，从最新往前取不超过条数/字符预算的消息；窗口不以孤立的 assistant 回复开头；被省略的旧消息以一条 system 说明代替（不篡改历史）。
- SHA-256：`37219c2adc08a4c7…` → `da4c158107160b6d…`
- 修改前：

        def reply(self, thread_id: str, content: str) -> dict[str, Any]:
    

- 修改后：

        def _context_window(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """Bounded short-term working memory sent to the model.
    
            Leading system messages are always kept; the most recent turns are kept
            within ``context_max_messages`` and ``context_max_chars``. Older turns stay
            in the persisted checkpoint and are only summarised as an omission note,
            never silently rewritten.
            """
            head: list[dict[str, Any]] = []
            index = 0
            while index < len(messages) and isinstance(messages[index], dict) and messages[index].get("role") == "system":
                head.append(messages[index])
                index += 1
            body = [item for item in messages[index:] if isinstance(item, dict)]
            budget = self.context_max_chars - sum(len(str(item.get("content", ""))) for item in head)
            kept: list[dict[str, Any]] = []
            for item in reversed(body):
                size = len(str(item.get("content", "")))
                if len(kept) >= self.context_max_messages or (kept and size > budget):
                    break
                kept.append(item)
                budget -= size
            kept.reverse()
            # Do not start the window with an orphaned assistant reply.
            while len(kept) > 1 and kept[0].get("role") == "assistant":
                kept.pop(0)
            omitted = len(body) - len(kept)
            window = list(head)
            if omitted > 0:
                window.append({
                    "role": "system",
                    "content": f"{omitted} earlier message(s) in this thread were omitted to keep the context bounded.",
                })
            window.extend(kept)
            return window
    
        def reply(self, thread_id: str, content: str) -> dict[str, Any]:
    

## [P1-RETRY] src/muse/model_client.py
- 时间：2026-11-25 17:03
- 问题：LMStudioClient 对瞬时故障（连接被拒、超时、429/5xx）没有任何重试/退避。
- 原因/依据：引入 time 供退避使用。
- SHA-256：`8518f6bf41eed7da…` → `4a02d768f7887cb3…`
- 修改前：

    import ipaddress
    import json
    from dataclasses import dataclass
    

- 修改后：

    import ipaddress
    import json
    import time
    from dataclasses import dataclass
    

## [P1-RETRY] src/muse/model_client.py
- 时间：2026-11-25 17:03
- 问题：同上。
- 原因/依据：新增可选参数 max_retries（默认 0，保持原行为与现有测试）、retry_backoff_seconds（默认 0.5s），带范围校验。
- SHA-256：`4a02d768f7887cb3…` → `a01e1b0605724a30…`
- 修改前：

            chat_template_kwargs: dict[str, Any] | None = None,
            bypass_proxies: bool = False,
        ) -> None:
            self.base_url = base_url.rstrip("/")
            self.model = model
            self.timeout = timeout
    

- 修改后：

            chat_template_kwargs: dict[str, Any] | None = None,
            bypass_proxies: bool = False,
            max_retries: int = 0,
            retry_backoff_seconds: float = 0.5,
        ) -> None:
            self.base_url = base_url.rstrip("/")
            self.model = model
            self.timeout = timeout
            if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 5:
                raise ValueError("max_retries must be an integer from 0 to 5")
            if not isinstance(retry_backoff_seconds, (int, float)) or isinstance(retry_backoff_seconds, bool) \
                    or not 0 <= retry_backoff_seconds <= 30:
                raise ValueError("retry_backoff_seconds must be between 0 and 30")
            self.max_retries = max_retries
            self.retry_backoff_seconds = float(retry_backoff_seconds)
    

## [P1-RETRY] src/muse/model_client.py
- 时间：2026-11-25 17:03
- 问题：同上。
- 原因/依据：请求改为有界重试循环：仅对瞬时错误重试（见 _is_transient），指数退避上限 8 秒；重定向/4xx/JSON 错误不重试，直接抛 ModelError。
- SHA-256：`a01e1b0605724a30…` → `2b52dea49095fcc6…`
- 修改前：

            request = urllib_request.Request(self.base_url + path, data=data, headers=headers, method=method)
            try:
                opener = self._opener.open if self._opener is not None else urllib_request.urlopen
                with opener(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise ModelError(f"Local model request failed: {exc}") from exc
    

- 修改后：

            request = urllib_request.Request(self.base_url + path, data=data, headers=headers, method=method)
            attempt = 0
            while True:
                try:
                    opener = self._opener.open if self._opener is not None else urllib_request.urlopen
                    with opener(request, timeout=self.timeout) as response:
                        result = json.loads(response.read().decode("utf-8"))
                    break
                except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                    if attempt < self.max_retries and self._is_transient(exc):
                        # Exponential backoff: 0.5s, 1s, 2s ... capped at 8s.
                        time.sleep(min(8.0, self.retry_backoff_seconds * (2 ** attempt)))
                        attempt += 1
                        continue
                    raise ModelError(f"Local model request failed: {exc}") from exc
    

## [P1-RETRY] src/muse/model_client.py
- 时间：2026-11-25 17:03
- 问题：同上。
- 原因/依据：新增 _is_transient：429/5xx、URLError、超时、连接错误视为瞬时；其余不重试。
- SHA-256：`2b52dea49095fcc6…` → `ff59a3d7f7402fc7…`
- 修改前：

        def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    

- 修改后：

        @staticmethod
        def _is_transient(exc: BaseException) -> bool:
            """Retry only failures that a later identical request may plausibly fix."""
            if isinstance(exc, HTTPError):
                return exc.code == 429 or 500 <= exc.code < 600
            if isinstance(exc, json.JSONDecodeError):
                return False
            return isinstance(exc, (URLError, TimeoutError, ConnectionError))
    
        def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    

## [P1-RETRY] src/muse/chat_cli.py
- 时间：2026-11-25 17:04
- 问题：交互式聊天入口在本地模型刚启动/短暂过载时直接失败。
- 原因/依据：对面向用户的 muse-chat 启用 2 次退避重试（0.5s、1s）；后台 Worker/Runtime 调用方保持原状（正由其他 Agent 修改，避免冲突）。
- SHA-256：`9400406d6a06c7e2…` → `81ab56f55a313120…`
- 修改前：

                LMStudioClient(args.base_url, model=args.model),
    

- 修改后：

                LMStudioClient(args.base_url, model=args.model, max_retries=2),
    

## [P2-CACHE] src/muse/cantonese_tts.py
- 时间：2026-11-25 17:04
- 问题：模块级 _PREFLIGHT_CACHE 每次 TTS 包更新（mtime/size/manifest 变化）新增一个键，常驻进程中无上限。
- 原因/依据：在已有锁内写入后按插入顺序淘汰最旧项，上限 16。
- SHA-256：`81380d23d185d588…` → `0b892e851404ff2e…`
- 修改前：

        with _TEMP_DIR_LOCK:
            _PREFLIGHT_CACHE[cache_key] = dict(result)
        return result
    

- 修改后：

        with _TEMP_DIR_LOCK:
            _PREFLIGHT_CACHE[cache_key] = dict(result)
            # Keys include package mtime/size and manifest digest, so every reinstall adds
            # a key; keep only the newest entries to bound long-running process memory.
            while len(_PREFLIGHT_CACHE) > 16:
                _PREFLIGHT_CACHE.pop(next(iter(_PREFLIGHT_CACHE)))
        return result
    

## [P1-MEMDEDUP] src/muse/memory.py
- 时间：2026-11-25 17:05
- 问题：MemoryStore.add 对完全相同的活动记忆重复插入，重复条目会挤占检索结果（search 按相关度排序后 LIMIT）。
- 原因/依据：插入前按 (project, kind, track, content) 精确查重，命中则返回已有记录（不改其来源/置信度，修订仍需走 revise）；已软删除的记忆不参与查重。approve_candidate 走独立 INSERT，未改动。
- SHA-256：`51afb53d9e5831a7…` → `3667d42e8b38ccd2…`
- 修改前：

            kind = self._validate_kind(kind)
            track = self._validate_track(track)
            cursor = self._connection.execute(
                """INSERT INTO memories
                   (project, content, source, confidence, kind, track, created_at, updated_at)

- 修改后：

            kind = self._validate_kind(kind)
            track = self._validate_track(track)
            # Deduplicate: an identical active memory (same project/kind/track/content)
            # is returned instead of creating a second copy that would skew retrieval.
            duplicate = self._connection.execute(
                "SELECT id FROM memories WHERE deleted_at IS NULL AND project = ? AND kind = ? "
                "AND track = ? AND content = ? ORDER BY id LIMIT 1",
                (project, kind, track, content),
            ).fetchone()
            if duplicate is not None:
                existing = self.get(int(duplicate[0]))
                if existing is not None:
                    return existing
            cursor = self._connection.execute(
                """INSERT INTO memories
                   (project, content, source, confidence, kind, track, created_at, updated_at)

## [P1-CONFIG] src/muse/config.py
- 时间：2026-11-25 17:05
- 问题：load_config 只检查段名存在：段被写成标量（如 memory = "x"）时抛 AttributeError，缺 [app].name / [scheduler].enabled 时 muse.main 抛 KeyError，均不在 main 的 (OSError, ValueError) 捕获范围内 → 未处理的 traceback。
- 原因/依据：校验各段为表、muse.main 实际读取的两个键存在且类型正确，统一抛 ValueError，由 main 输出 'Configuration check failed' 并返回 2。
- SHA-256：`8ca471b43711ba76…` → `ea67d5f7277a129f…`
- 修改前：

        if missing:
            raise ValueError(f"Missing configuration sections: {', '.join(missing)}")
    

- 修改后：

        if missing:
            raise ValueError(f"Missing configuration sections: {', '.join(missing)}")
        not_tables = [section for section in required_sections if not isinstance(data[section], dict)]
        if not_tables:
            raise ValueError(f"Configuration sections must be tables: {', '.join(not_tables)}")
        for section, key in (("app", "name"), ("scheduler", "enabled")):
            if key not in data[section]:
                raise ValueError(f"Missing configuration key: [{section}].{key}")
        if not isinstance(data["scheduler"]["enabled"], bool):
            raise ValueError("[scheduler].enabled must be true or false")
    

## [P2-README] README.md
- 时间：2026-11-25 17:13
- 问题：README 未提及一键体检批处理。
- 原因/依据：补充 run_smoke.bat 用法一行。
- SHA-256：`a3b563d591e9f410…` → `c483bc25c867d6f7…`
- 修改前：

    & ".\.venv\Scripts\python.exe" "scripts\muse_smoke.py" --full   # 冒烟 + 全量 unittest
    

- 修改后：

    & ".\.venv\Scripts\python.exe" "scripts\muse_smoke.py" --full   # 冒烟 + 全量 unittest
    & "<MUSE_ROOT>\run_smoke.bat"                              # 一键：自动 .venv + UTF-8 + 21 阶段分层体检与通过率汇总
    

## [MIND-API] src/muse/status_api.py
- 时间：2026-11-25 17:31
- 问题：Status API 没有暴露意识循环（内在状态/循环日志/待审批步骤）的任何端点，API 层与新主循环脱节。
- 原因/依据：新增只读 GET /api/v1/mind/state（沿用既有 loopback + Bearer + Origin 校验链）；底层 read_mind_status 以 SQLite 只读 URI 打开，不建库、不写状态。
- SHA-256：`4ecae3d999282939…` → `8668dc480afdf8eb…`
- 修改前：

                    elif parsed.path == "/api/v1/system-dashboard":
                        self._send(200, build_system_dashboard(workspace_root, capability_path))
    

- 修改后：

                    elif parsed.path == "/api/v1/mind/state":
                        # Read-only: inner state, recent mind cycles and pending mind_step approvals.
                        from muse.consciousness_loop import read_mind_status
                        self._send(200, {"api_version": 1, **read_mind_status(workspace_root)})
                    elif parsed.path == "/api/v1/system-dashboard":
                        self._send(200, build_system_dashboard(workspace_root, capability_path))
    

## [ORPHAN-CLI] pyproject.toml
- 时间：2026-11-25 17:31
- 问题：6 个模块已实现 main()/argparse 但未注册为命令（孤岛），另新增 muse-mind 主循环入口。
- 原因/依据：注册 muse-mind、muse-candidate-enqueue、muse-chimera-voice、muse-quotes、muse-video-editable-export、muse-video-reviewer-signer、muse-portal-admin；均已核对 main 在 parse_args 前无副作用。
- SHA-256：`a2798596cb1bf284…` → `0f06e2553c626b64…`
- 修改前：

    muse-value = "muse.value_forge:main"
    

- 修改后：

    muse-value = "muse.value_forge:main"
    # Added 2026-11-25: mind loop + previously unregistered module CLIs (each already had main()).
    muse-mind = "muse.consciousness_loop:main"
    muse-candidate-enqueue = "muse.candidate_enqueuer:main"
    muse-chimera-voice = "muse.chimera_vocal:main"
    muse-quotes = "muse.quote_intelligence:main"
    muse-video-editable-export = "muse.video_editable_export:main"
    muse-video-reviewer-signer = "muse.video_reviewer_signer:main"
    muse-portal-admin = "muse.web_portal_admin:main"
    

## [CONFIG] .env.example
- 时间：2026-11-25 17:31
- 问题：新增 muse-mind 读取的环境变量未登记。
- 原因/依据：补 MUSE_MIND_LLM_URL / MUSE_MIND_MODEL 占位符。
- SHA-256：`9c2c9c0dbca85939…` → `0e3f6de070e3022f…`
- 修改前：

    MUSE_MEMPALACE_ENABLED=0
    

- 修改后：

    MUSE_MEMPALACE_ENABLED=0
    
    # --- Mind loop (muse-mind); empty = deterministic mode without a model ---
    MUSE_MIND_LLM_URL=http://127.0.0.1:1234/v1
    MUSE_MIND_MODEL=<local-model-id>
    

## [PERF-IDX] src/muse/task_queue.py
- 时间：2026-11-25 17:49
- 问题：runtime_estimate_seconds() 在每次未指定 estimated_seconds 的 enqueue 时执行 task_events JOIN tasks + ORDER BY created_at DESC LIMIT 4000，无可用索引 → 账本增大后全表扫描+排序；沙箱并发压测中该查询触发 MemoryError。
- 原因/依据：仅新增 CREATE INDEX IF NOT EXISTS idx_task_events_type_time(event_type, created_at)，不改查询与语义；对已有数据库为一次性增量建索引。
- SHA-256：`37dad934da7a7a88…` → `88e4e39e87c4250f…`
- 修改前：

            self._connection.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_time ON task_events(task_id, created_at, event_id)")
            self._connection.commit()
    

- 修改后：

            self._connection.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_time ON task_events(task_id, created_at, event_id)")
            # Additive index for runtime_estimate_seconds(), which runs on every enqueue without an explicit
            # estimate: filter by event_type and read newest-first without a full scan + sort.
            self._connection.execute("CREATE INDEX IF NOT EXISTS idx_task_events_type_time ON task_events(event_type, created_at)")
            self._connection.commit()
    

## [P2-README] README.md
- 时间：2026-11-25 17:52
- 问题：README 缺少全量诊断与意识主循环的使用说明。
- 原因/依据：新增 run_full_diag.ps1 与 muse-mind 用法、数据位置与安全边界说明。
- SHA-256：`c483bc25c867d6f7…` → `11b445c6f3eabdb8…`
- 修改前：

    **依赖与配置**：

- 修改后：

    **全量诊断（推荐）**：一次运行原冒烟测试 + 6 层全量诊断（静态编译/未定义名、全模块与测试导入、配置与持久化、子系统单元、离线 Mock LLM 全脑闭环、API 全路由），汇总到 `reports\qw_smoke\latest.log`、`latest.json`、`diag_matrix.json`：
    ```powershell
    powershell -NoProfile -ExecutionPolicy Bypass -File "<MUSE_ROOT>\scripts\run_full_diag.ps1"
    ```
    
    **意识主循环 `muse-mind`**（感知 → 内在状态 → 记忆检索 → 反思 → 规划 → 审批门 → 执行 → 核验 → 经验固化；无模型时走确定性路径，可选本地模型 `--llm-url http://127.0.0.1:1234/v1`）：
    ```powershell
    $env:PYTHONPATH = "<MUSE_ROOT>\src"; cd "<MUSE_ROOT>"
    py -3.11 -m muse.consciousness_loop perceive "请帮我规划一个离线复盘工具"   # 低风险步骤按策略自动执行，开发/运维/高危步骤等待人工审批
    py -3.11 -m muse.consciousness_loop status                                 # 内在状态、最近循环、待审批步骤
    py -3.11 -m muse.consciousness_loop approve <TASK_ID> --by 你的名字          # 人工批准；reject 会级联取消下游步骤
    py -3.11 -m muse.consciousness_loop run --interval 60                      # 前台自治心跳（Ctrl+C 停止）
    ```
    状态保存在 `data\mind\`（state.json 原子写 + .bak 自动回退、journal.sqlite3），只读接口 `GET /api/v1/mind/state`。执行器只写带 SHA-256 的工作包，不运行命令、不联网、不安装、不发送。
    
    **依赖与配置**：

## [NEW] 本轮新增文件（2026-11-25 17:52）
- `src/muse/structured_output.py`：模型输出多层 JSON 提取（严格→代码块→平衡括号→保守修复）、拒绝重复键、Schema 子集校验、修复重试 `request_structured`、线程安全 `CircuitBreaker`、指数退避 `call_with_retry`。
- `src/muse/inner_state.py`：内在状态控制模型（valence/energy/curiosity、稳态衰减、事件效应、滚动小时预算节流、目标权重与防固着、自适应 max_actions、自治门 should_think）；原子写 + .bak 回退 + 只读快照。
- `src/muse/consciousness_loop.py`：意识主循环 MindLoop + MindJournal + MindStepHandler + CLI `muse-mind`；接入 MemoryStore / CommanderPlanner / TaskQueue / worker.run_once / IndependentReviewGate / FailureTraceRecorder(原孤岛 trace) / SkillifyService(原孤岛 skillify)；审批超时与级联取消、重启恢复、`read_mind_status`。
- `tests/test_consciousness_loop.py`：10 个单元/集成测试（真实 worker.run_once）。
- `scripts/muse_full_diag.py`、`scripts/run_full_diag.ps1`：全量诊断引擎与一键脚本。
- 沙箱实测：新诊断 6 阶段 74/74、新单测 10/10、回归 test_assistant 4/4、test_memory 18/18、test_conversations_and_model_client 7/7、配置边界 7/7、冒烟 S4/L3.1–L3.5/L4.2 共 44/44（Worker 以忠实替身代替，真机仍需复验）。

## [ENV-VENV] scripts/Muse-Start.ps1
- 时间：2026-11-25 18:12
- 问题：实测：.venv\Scripts\python.exe 启动即报 "No pyvenv.cfg file" 退出（pyvenv.cfg 实际存在）。Muse-Start 仅检查文件存在，随后在 Install-Muse-Tasks/计划任务/状态查询中以难以理解的方式失败。
- 原因/依据：启动前真实探测解释器（python -c），失败时给出明确原因与离线修复命令 Repair-MuseVenv.ps1 -Apply；不改变正常路径行为。
- SHA-256：`b545e63e986f07b8…` → `521b7f39e15f1caa…`
- 修改前：

    if (-not (Test-Path $Python)) { throw "Muse Python environment not found: $Python" }
    

- 修改后：

    if (-not (Test-Path $Python)) { throw "Muse Python environment not found: $Python" }
    . (Join-Path $PSScriptRoot 'Resolve-MusePython.ps1')
    $PythonProbe = Test-MusePython -Exe $Python
    if (-not $PythonProbe.Ok) { throw "Muse .venv launcher is unusable ($($PythonProbe.Detail)). Repair offline: powershell -NoProfile -ExecutionPolicy Bypass -File `"$Root\scripts\Repair-MuseVenv.ps1`" -Apply" }
    

## [ENV-VENV] README.md
- 时间：2026-11-25 18:12
- 问题：README 无 venv 启动器损坏的处理说明。
- 原因/依据：新增常见问题条目。
- SHA-256：`11b445c6f3eabdb8…` → `28e6fe58978dfcc4…`
- 修改前：

    - 路径含空格：所有命令中的 `<MUSE_ROOT>` 必须加双引号。
    

- 修改后：

    - 路径含空格：所有命令中的 `<MUSE_ROOT>` 必须加双引号。
    - `.venv\Scripts\python.exe` 报 `No pyvenv.cfg file`：虚拟环境启动器损坏（Muse-Start、计划任务都会受影响）。先诊断 `powershell -NoProfile -ExecutionPolicy Bypass -File "<MUSE_ROOT>\scripts\Repair-MuseVenv.ps1"`，确认后加 `-Apply` 离线修复（备份原启动器，保留已装包）。`run_full_diag.ps1` / `run_smoke.bat` 会自动回退到可用解释器继续诊断。
    

## [ENV-VENV] 首次真机运行结果与修复（2026-11-25 18:12）
- 真机日志 reports/qw_smoke/latest.log 仅两行 `No pyvenv.cfg file`：`.venv\Scripts\python.exe`（uv 0.13 创建的 venv 启动器）启动即失败，两个 Python 脚本均未执行；pyvenv.cfg 实际存在（home=…\Python311, 3.11.9）。影响面：Muse-Start.ps1、Muse-Stop.ps1、muse-supervisor.cmd、Install-Muse-Tasks.ps1 注册的两个计划任务全部依赖该启动器。
- 新增 `scripts/Resolve-MusePython.ps1`：逐个真实启动候选解释器（.venv → pyvenv.cfg home → py -3.11/-3.12/-3.13/-3 → PATH python），同小版本回退时把 .venv\Lib\site-packages 加入 PYTHONPATH；所有探测结果写入日志。
- 新增 `scripts/Repair-MuseVenv.ps1`：默认只诊断；`-Apply` 时先备份启动器与 pyvenv.cfg 到 .qw-backups/venv-<时间>，再离线执行 `<base> -m venv --upgrade`（不联网、不动 site-packages）。
- 重写 `scripts/run_full_diag.ps1`（使用解析器，新增 -SmokeOnly/-Quick，探测记录附在 latest.log 末尾）；`run_smoke.bat` 改为委托 ps1，单一代码路径。
- `scripts/Muse-Start.ps1`：启动前真实探测解释器，失败时给出原因与修复命令（备份 .orig）。

## [B391-R1] src\muse\status_api.py
- 时间：2026-11-27 09:25
- 问题：未传入 store_health 时 /api/v1/health 返回 503，导致本机控制台/Web Portal/独立 Status API 全部显示未连接；删除重复分支
- SHA-256：`dba70a6c29af1007…` → `4773711143017783…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R4b] src\muse\autonomous_runtime.py
- 时间：2026-11-27 09:25
- 问题：_video_worker 总取最新一张排队票，且未捕获 RuntimeBlocked：一张校验失败但仍 queued 的票会永久卡住后续票
- SHA-256：`90aea043fb133598…` → `1e1c5efec6d6e846…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R4b] src\muse\autonomous_runtime.py
- 时间：2026-11-27 09:25
- 问题：逐张尝试并跳过领取前被 Commander 拒绝的票（保持 queued 供人工排查），每 tick 最多派发一张
- SHA-256：`1e1c5efec6d6e846…` → `357497e76eccf1d5…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R1-test] tests\test_status_api.py
- 时间：2026-11-27 09:25
- 问题：新增：未配置 store_health 时 health=200；显式不健康时 health/store-health 均 503
- SHA-256：`d6eb6b422576fca4…` → `e7e2ccf073072859…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R4b-test] tests\test_autonomous_runtime.py
- 时间：2026-11-27 09:25
- 问题：新增：领取前被拒的票被跳过且保持 queued，下一张有效票被派发；全部被拒时返回 blocked
- SHA-256：`0ec744e6079e1208…` → `82063bfb064b389b…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R3] ui-prototype\api.js
- 时间：2026-11-27 09:26
- 问题：api.js 两条语句被拼在同一行（与 N1 同类写入损坏），拆回独立行；无逻辑变化
- SHA-256：`77e2509743ff71ba…` → `0af71a53e41e2b83…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R3] ui-prototype\api.js
- 时间：2026-11-27 09:26
- 问题：api.js 两条语句被拼在同一行（与 N1 同类写入损坏），拆回独立行；无逻辑变化
- SHA-256：`0af71a53e41e2b83…` → `641a328e09597c3d…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R3] ui-prototype\api.js
- 时间：2026-11-27 09:26
- 问题：api.js 两条语句被拼在同一行（与 N1 同类写入损坏），拆回独立行；无逻辑变化
- SHA-256：`641a328e09597c3d…` → `f73f83aa6a5ee27b…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R3] ui-prototype\api.js
- 时间：2026-11-27 09:26
- 问题：api.js 两条语句被拼在同一行（与 N1 同类写入损坏），拆回独立行；无逻辑变化
- SHA-256：`f73f83aa6a5ee27b…` → `03c89b3755328b5b…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R2] ui-prototype\src\App.svelte
- 时间：2026-11-27 09:26
- 问题：AI 审批接口只接受 2–5 秒，界面仍可选 1 秒导致 400；删除 1 秒选项
- SHA-256：`ce9347ad40393106…` → `d459d3fcb885bee6…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R5] ui-prototype\src\App.svelte
- 时间：2026-11-27 09:26
- 问题：本地路径旁白稿已必填，选择“音轨用途=旁白”时后端会拒绝（音频只能做背景音乐）；移除该选择器
- SHA-256：`d459d3fcb885bee6…` → `19f8286df104fd38…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R5] ui-prototype\src\App.svelte
- 时间：2026-11-27 09:26
- 问题：本地路径有旁白稿时音频文件固定作为背景音乐提交
- SHA-256：`19f8286df104fd38…` → `995e5b4d331d951c…`（原件 `.qw-backups/20261127-b391/`）

## [B391-R4a] src\muse\status_api.py
- 时间：2026-11-27 09:26
- 问题：审批人固定为 local-dashboard-user：本机无多用户账号体系，改为在响应中如实标注身份范围（local_single_user_session），不伪造身份
- SHA-256：`4773711143017783…` → `ebcaec35996c0bac…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P1] src\muse\status_api.py
- 时间：2026-11-27 09:35
- 问题：挂载 control_center_api 上下文
- SHA-256：`ebcaec35996c0bac…` → `0ed723244b9ff1e1…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P1] src\muse\status_api.py
- 时间：2026-11-27 09:35
- 问题：GET /api/v1/control/*
- SHA-256：`0ed723244b9ff1e1…` → `f08ce8556155bb41…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P1] src\muse\status_api.py
- 时间：2026-11-27 09:35
- 问题：POST /api/v1/control/*（loopback + Origin + Bearer 已在入口校验；JSON 限 16 KiB、拒绝重复键）
- SHA-256：`f08ce8556155bb41…` → `43264a763b099480…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P1-label] src\muse\status_api.py
- 时间：2026-11-27 09:35
- 问题：玄学类接口（/api/v1/metaphysics/*、/api/v1/omega/xuanshu/*、/api/v1/control/xuanshu/*）2xx 响应自动加「玄学推演／待验证假设」标签，强制 is_evidence/is_prediction=false
- SHA-256：`43264a763b099480…` → `ff0741b3b828be84…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P2] ui-prototype\vite.config.js
- 时间：2026-11-27 09:48
- 问题：构建入口新增 portal.html（/portal）与 os.html（/os）
- SHA-256：`dad95cf92ab19974…` → `8a986049459368bd…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P2] src\muse\local_dashboard.py
- 时间：2026-11-27 09:48
- 问题：本机控制台支持 /portal 与 /os 无扩展名路由（桌面网关已有同等映射）
- SHA-256：`bd4c101f5bc304ba…` → `4eb06168fc2a93f3…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P2] ui-prototype\src\App.svelte
- 时间：2026-11-27 10:12
- 问题：主控台导航栏新增门户（/portal）与 Super Agent OS（/os）入口
- SHA-256：`995e5b4d331d951c…` → `3ca74e7188abc40e…`（原件 `.qw-backups/20261127-b391/`）

## [B391-P2] src\muse\omega_api.py
- 时间：2026-11-27 10:12
- 问题：Omega 导航新增门户与 OS（页面无内联脚本/样式/事件，满足 test_omega_security_contract 的 CSP 约束）
- SHA-256：`bf2ac301ec073902…` → `2c33f4f72a46a76b…`（原件 `.qw-backups/20261127-b391/`）

# B391-P2 · 第二轮饱和攻坚（2026-11-27 11:39–12:26，AWS Quick）

> 编号说明：本轮编号 `B391-P2-01…28`（P2 = 第二轮，对应备份目录 `.qw-backups/20261127-b391-p2/`），与上一轮界面阶段标签 `[B391-P2]` 不同。
> 本轮首次触碰的每个文件都先原样备份到 `.qw-backups/20261127-b391-p2/<路径>.orig`（SHA-256 见同目录 MANIFEST.txt）；上一轮 `20261127-b391/` 基线备份未被覆盖。未执行任何 git 命令。
> 证据层级：以下全部为 L1 + L2/L2.5（云端沙箱真实执行）；L3–L5 需真机。

## [B391-P2-01/03] src\muse\control_center_api.py
- 时间：2026-11-27 12:26
- 问题/改动：JSON 请求体此前接受 NaN/Infinity/1e999（解析为非有限浮点）且不限嵌套深度；新增 parse_json_object：拒绝非有限数、嵌套 >32 层、BOM、顶层非对象、重复键，统一 400 + 可读原因
- 回归用例：test_b391_p2_hardening.JsonBoundaryTests（5 例）
- SHA-256：`f9a3930e317a419e…` → `b487e406d30af6b6…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-05/06/07/08] src\muse\control_center_api.py
- 时间：2026-11-27 12:26
- 问题/改动：①并发同 request_id 提交时两个请求都返回 201（同一张票被报告为“新建”两次）→ 进程内锁 + UNIQUE 冲突回放/409；②RETRY 的 request_id 复用到另一张 Ticket 时静默回放别的票 → 409；③重试丢失 workload_class → 保留；④重试会剥掉 `_signed_review` 等专用门禁标记、生成无门禁新票 → 拒绝并指向专用流程
- 回归用例：IdempotencyTests（4 例）、DedicatedGateTests
- SHA-256：`f9a3930e317a419e…` → `b487e406d30af6b6…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-10] src\muse\control_center_api.py
- 时间：2026-11-27 12:26
- 问题/改动：新增 POST /control/creation/feedback（LINK-FEEDBACK-TO-CLAIM）：创作账本 + Research Wiki `claim —tested_by→ 实测反馈记录`，按 request_id 双库幂等、改载荷 409；成片 SHA-256 由服务器从 Product Vault 计算（客户端不能提供）；claim 状态与玄学本体不被改写。新增 GET /control/knowledge/claims 与 claims/<id>/tested-by（原始假设来源与实测反馈分栏）
- 回归用例：TestedByLoopTests（4 例）+ TestedByHttpEndToEndTests（真实 HTTP 全链路）
- SHA-256：`f9a3930e317a419e…` → `b487e406d30af6b6…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-12] src\muse\control_center_api.py
- 时间：2026-11-27 12:26
- 问题/改动：Obsidian 审计：audit_vault() 把计数放在顶层，接口却读不存在的 counts 键 → 界面永远没有计数、conflicts 恒为 null；改为读取真实字段
- 回归用例：ObsidianAuditTests
- SHA-256：`f9a3930e317a419e…` → `b487e406d30af6b6…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-10/11] src\muse\status_api.py
- 时间：2026-11-27 12:26
- 问题/改动：接入 Product Vault 解析器（哈希校验 + 服务器侧 SHA-256）与创作账本写入器；成片 preview/video 支持单段 Range：206 + Content-Range、越界 416 + `bytes */size`、空文件不除零、多段/畸形 Range 回退 200；新增 parse_byte_range；顺带拆开第 3057 行拼行（AST 等价）
- 回归用例：ByteRangeParserTests、VaultRangeHttpTests、TestedByHttpEndToEndTests
- SHA-256：`ff0741b3b828be84…` → `9014eb8de6783e27…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-13] src\muse\obsidian_vault.py
- 时间：2026-11-27 12:26
- 问题/改动：只读镜像审计把带 UTF-8 BOM 的笔记误报为冲突（malformed_managed_note）；读取改用 utf-8-sig。CRLF 与反斜杠路径经实测本来就不会误报，未改动
- 回归用例：ObsidianAuditTests（LF/CRLF/BOM/真实修改 四种）
- SHA-256：`43986e8404e349cd…` → `0ea0ab043d28b57e…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-14] src\muse_sidecar_main.py
- 时间：2026-11-27 12:26
- 问题/改动：桌面网关代理把整段响应 read() 进内存（成片最大 2 GiB）；>8 MiB 或 video/audio/zip 响应改为按块转发并透传 Content-Length，Range/206 头保持
- 回归用例：GatewayStreamingTests（≈10 MiB 实测哈希一致 + 206 透传）
- SHA-256：`dd5a621070fc4bd0…` → `910c11e3d8014bad…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-09] src\muse\autonomous_runtime.py
- 时间：2026-11-27 12:26
- 问题/改动：两处语句粘连拆行（for 循环体、if…break），AST 完全一致，无逻辑变化
- SHA-256：`357497e76eccf1d5…` → `7703aee558ced6f3…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-09] ui-prototype\src\App.svelte
- 时间：2026-11-27 12:26
- 问题/改动：修复第 128 行历史拼行（两个 let 声明），并拆开 5 处标签粘连行；保持 CRLF；去空白后内容逐字节一致
- SHA-256：`3ca74e7188abc40e…` → `387fef93df0e7c09…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-09] tests\test_status_api.py
- 时间：2026-11-27 12:26
- 问题/改动：4 处表达式粘连拆行，AST 一致
- SHA-256：`e7e2ccf073072859…` → `f18eb13a883d6e52…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-09] tests\test_autonomous_runtime.py
- 时间：2026-11-27 12:26
- 问题/改动：1 处表达式粘连拆行，AST 一致
- SHA-256：`82063bfb064b389b…` → `f1f1ce75b70c6d2d…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-28] tests\test_b391_control_center.py
- 时间：2026-11-27 12:26
- 问题/改动：TemporaryDirectory 改为 ignore_cleanup_errors=True，避免 Windows 杀软/索引短暂占用导致的 WinError 32 假失败
- SHA-256：`427aa979409bf668…` → `a7bc739dd23be531…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-01..14] tests\test_b391_p2_hardening.py
- 时间：2026-11-27 12:26
- 问题/改动：新增 20 个对抗式回归用例（先写复现断言：5 例在旧代码首跑即红；并发用例在压测中复现“双 201”；Obsidian 两项先手工复现再固化为用例；修复后全绿）
- SHA-256：`—` → `82916c76dc0742fc…`（原件 `.qw-backups/20261127-b391-p2/`；本轮新建）

## [B391-P2-18] scripts\b391_l25_contract.py
- 时间：2026-11-27 12:26
- 问题/改动：新增 L2.5 契约核验器：启动真实 Status API（临时库），回放 os.js/portal.js 全部 60 个调用点；DOM id 引用/重复、CSP（内联 script/style/on*）、确认短语前后端一致性一并核验
- SHA-256：`—` → `0e1158db538f1b6d…`（原件 `.qw-backups/20261127-b391-p2/`；本轮新建）

## [B391-P2-15] scripts\b391_verify.py
- 时间：2026-11-27 12:26
- 问题/改动：验收脚本装甲：环境快照（Python/Node/npm/FFmpeg/ffprobe/Remotion/TTS/ASR/ComfyUI/GPU/编码/路径空格）、每步后原子落盘 reports/b391_acceptance/LATEST_REPORT.{json,md}（兼容旧 verify_latest）、失败用例完整堆栈、输出末尾 40 行、样片 MP4 SHA-256、npm 经 node npm-cli.js 调用、全部列表传参 shell=False
- SHA-256：`708ee5d58eba0c0b…` → `f53d6ed4eff283c5…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-16] 3_双击B391验收.bat
- 时间：2026-11-27 12:26
- 问题/改动：显式 PYTHONUTF8/PYTHONIOENCODING/PYTHONDONTWRITEBYTECODE、仅在 .venv 存在时探测、报告路径改为 b391_acceptance；保持 CRLF、无 BOM、块内 echo 无 ASCII 括号
- SHA-256：`094b28daa6024ae8…` → `859e057999f97756…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-17] ui-prototype\package.json
- 时间：2026-11-27 12:26
- 问题/改动：`node --test *.test.js` 依赖 shell 展开通配符，Windows cmd 不展开（Node 20 会报找不到文件）；改为 `node --test` 默认发现
- SHA-256：`d3ab690eb13c57c6…` → `34d9377b22e91921…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-19] docs\B391_L5_PATH_B_RUNBOOK.md
- 时间：2026-11-27 12:26
- 问题/改动：新增路径 B（L5）实机作战卡：端口探测、提单参数、唯一人工批准位置、Worker 开关与观察点、失败日志路径、判定标准
- SHA-256：`—` → `93981c4aaa00de26…`（原件 `.qw-backups/20261127-b391-p2/`；本轮新建）

## [B391-P2-20] ui-prototype\muse-os-logic.js
- 时间：2026-11-27 12:26
- 问题/改动：lineDiff：空文本按 0 行处理（首版修订不再出现幽灵删除行）；新增纯函数 phraseProgress / pollDelay / describeApiError / rowsChanged / evidenceChainView
- 回归用例：muse-os-logic.test.js（12→17 例，68 个断言）
- SHA-256：`db3be2b78a29a9f5…` → `e2f8b0b881d06558…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-20] ui-prototype\muse-os-logic.test.js
- 时间：2026-11-27 12:26
- 问题/改动：更新 lineDiff 期望并新增 5 组用例
- SHA-256：`4afe147efa780625…` → `c4c4e7a592dda168…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-21] ui-prototype\omega-common.js
- 时间：2026-11-27 12:26
- 问题/改动：call() 失败时只抛后端文本、无状态码，网络错误/超时无中文说明；新增 ApiError（`HTTP 409 · 后端原因（含义）`）、30 s/60 s 超时、guarded() 加 aria-busy
- SHA-256：`fa88a0e59c6f301e…` → `77f9849937980e8e…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-22] ui-prototype\cosmos-fx.js
- 时间：2026-11-27 12:26
- 问题/改动：星轨：resize 去抖、页面隐藏时暂停 rAF、返回 destroy() 释放全部监听
- SHA-256：`261ca7542a160778…` → `ed63b6c32bbc2c08…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-23/24/26/27] ui-prototype\os.js
- 时间：2026-11-27 12:26
- 问题/改动：①统一确认门禁弹窗：显示后端校验的精确短语、“一键填入”（仍需人工点确认）、实时拼写校验、错误原位显示（含 HTTP 码）、防连点；替换全部 window.confirm/prompt 写操作；②成片反馈可关联假设 → tested_by，知识图谱 claim 抽屉显示证据链；③后台刷新：数据未变不重绘、用户输入中/弹窗打开时跳过、页面隐藏降频、恢复可见立即同步；④resize 去抖且不再每次重新请求图谱；Esc 关闭弹窗/抽屉；记忆检索防抖、图谱回车检索；⑤就绪度缓存永不过期（装好 ComfyUI 也不更新）→ 60 s TTL；⑥成片库刷新不再清空反馈草稿/中断播放，播放改走 inline preview；⑦图谱画布 DPR 上限 3（4K 清晰）；⑧退出页面清理定时器
- SHA-256：`dbf36e9fb669cb93…` → `3224426a49738ccf…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-23] ui-prototype\os.html
- 时间：2026-11-27 12:26
- 问题/改动：新增确认门禁对话框（aria-modal）；检索框 type=search；抽屉可聚焦；文案更新
- SHA-256：`86bd06737ed6e1b1…` → `8f1427a18591d14e…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-24] ui-prototype\muse-cosmos.css
- 时间：2026-11-27 12:26
- 问题/改动：焦点可见环、忙碌态、门禁对话框、证据链样式、表格长文本换行、1281–1600 px 与 ≥2200 px 布局
- SHA-256：`7b8f0f51bb5cd3a0…` → `45b8b35ec978361e…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-25] ui-prototype\portal.js
- 时间：2026-11-27 12:26
- 问题/改动：门户每 30 s 轮询都会重建 <video>，正在播放的成片被打断；改为成片未变化则不重绘，并按页面可见性调节轮询
- SHA-256：`48369003057d2b61…` → `dd9d33e14dca24c6…`（原件 `.qw-backups/20261127-b391-p2/`）

## [B391-P2-29] src\muse\status_api.py
- 时间：2026-11-27 17:03（真机验收 15:04–15:41 的 full_suite 失败）
- 问题：`do_POST` 内另有两处函数内 `from muse.task_queue import TaskQueue`（另一程序早先写入），使 `TaskQueue` 在整个函数里成为局部变量；`POST /api/v1/inspirations/evolve` 分支在这两行之前使用它 → 每次都 `UnboundLocalError`、服务端线程崩溃、连接被断开（真机经系统代理 127.0.0.1:7890 表现为 HTTP 502）。云端已复现。
- 修复：只在该分支补一行同样的函数内导入（保持其他分支的晚绑定行为——`test_status_api` 依赖它做 mock；整体删除这些导入会打破该契约，已试并回退）。
- 回归用例：`test_b391_p2_hardening.ShadowedImportGuardTests`（AST 守卫：旧代码精确报 `do_POST():3715`，修复后为空；含自检样例）；`test_inspiration_evolution.test_status_api_evolves_inspiration_but_blocks_video_without_persisted_commander_plan` 云端已越过 202 断言（后续步骤受沙箱 sqlite backup 限制，需真机复验）。
- SHA-256：`9014eb8de6783e27…` → `81e812a6ec80e2fb…`（本轮前原件见 `.qw-backups/20261127-b391-p2/src/muse/status_api.py.orig`；本次修改前版本另存 `status_api.py.p2b.orig`）

## [B391-P2-30] scripts\b391_verify.py
- 问题：定向测试在 `tests/` 目录下运行，PYTHONPATH 缺项目根目录 → `test_unified_runtime` 的 `from tests.video_reviewer_test_support import …` 报 `No module named 'tests'`（全量测试从根目录运行，故未受影响）。
- 修复：PYTHONPATH 首位加入项目根目录。

## [B391-P2-31] scripts\b391_verify.py
- 问题：本机设置了系统代理 127.0.0.1:7890，测试子进程的本机回环请求可能经过代理（上条 502 即经代理返回）。
- 修复：子进程设置 `NO_PROXY/no_proxy=127.0.0.1,localhost,::1`（urllib 因此只用环境变量代理设置，回环流量始终直连）。

## [B391-P2-32] scripts\b391_verify.py
- 问题：真实样片失败 `video assembly failed: waiting for the Muse model supervisor to pause its managed model process`。取证（只读）：模型监督器在线（心跳实测每 30 s）、本地模型健康、且不是监督器启动的子进程（child_pid 为空），即外部模型（LM Studio :1234 在监听）；GPU 租约 15:40:35 获取、15:41:52 释放（等满 75 s）。按设计 Muse 不会停止外部进程，因此样片拿不到 GPU。监督器在租约期间为何没有写入新状态尚未查清（代码逻辑应写 external_model_active 并立即报“无法让出 GPU”），已如实标为未知。
- 修复：样片步骤前加入 GPU 只读预检：外部模型占用或已有租约时立即失败并给出中文操作指引，不再空等 75 s；不改变任何 GPU/模型行为。
- 回归：云端 5 种状态（无库/外部模型/托管模型/模型离线/已有租约）结果全部符合预期。
- SHA-256（b391_verify.py，P2-30/31/32 合计）：`f53d6ed4eff283c5…` → `d287117f90e6ffae…`；本次修改前版本另存 `.p2b.orig`

## [B391-P2-33] tests\test_b391_p2_hardening.py
- 新增 `ShadowedImportGuardTests`（2 例）；云端全套 22/22。
- SHA-256：`82916c76dc0742fc…` → `e31641ec9fa0d8e2…`

### 真机验收（L3/L4）第 1 次结果（2026-11-27 15:04–15:41，报告 reports/b391_acceptance/LATEST_REPORT.md）
- 24 步中 20 步通过：compileall、17 个定向套件中 16 个、L2.5 契约/DOM/CSP、冒烟、npm test、npm run build（portal/os 均已产出）。
- 全量 3,045 个用例中仅 1 个错误（即 P2-29）。
- 失败 3 步：unit_test_unified_runtime（P2-30）、full_suite（P2-29）、real_local_sample（P2-32，环境：外部模型占用 GPU）。
- 环境快照：Python 3.11.9、Node v24.20.0、npm 11.19.0、FFmpeg 8.1.1、Remotion 4.0.543、CantoTTS/ASR 模型齐备、ComfyUI 未运行、GPU AMD Radeon RX 6750 GRE 12GB。

## [B391-P2-34] src\muse\store_health.py
- 时间：2026-11-27 17:37（真机验收第 2 次 17:06–17:18 的 full_suite 失败）
- 问题：启动自检 `_probe_sqlite` 用 `with sqlite3.connect(...) as connection:`。Python 的 sqlite3 连接上下文只提交/回滚、**不关闭**连接，文件句柄要等垃圾回收才释放；Windows 上临时目录删除时偶发 `WinError 32`（`data\agents\registry.sqlite3` 被占用）→ `test_muse_sidecar_feedback` 在全量测试中失败（同一用例在定向测试中通过，故为偶发）。桌面引擎每次启动也会遗留这 3 个句柄。
- 修复：改为 `with closing(sqlite3.connect(...))`。全仓 src/ 与 scripts/ 扫描：此写法仅此 1 处。
- 回归用例：`test_b391_p2_hardening.StoreProbeClosesConnectionsTests`（旧代码红：连接未关闭；修复后绿）；云端全套 23/23。
- SHA-256：`25d9f6cf367d15cc…` → `49b60986e39b7d5f…`（原件 `.qw-backups/20261127-b391-p2/src/muse/store_health.py.orig`）
- 测试文件 SHA-256：`e31641ec9fa0d8e2…` → `2e401f4e0a076747…`（改前版本 `.p2c.orig`）

### 真机验收第 2 次（2026-11-27 17:06–17:18）
- 22 步通过（含 unit_test_unified_runtime 80/80，P2-30 生效；P2-29 修复后灵感进化用例通过）；全量 3,047 用例仅 1 个偶发错误（即 P2-34）。
- real_local_sample：GPU 预检（P2-32）按预期立即拦截——LM Studio :1234 仍在运行，需用户先卸载/退出。

### 真机验收第 3 次（2026-11-27 17:54–18:06）
- 全量 3,048 用例 0 失败，24 步中 23 步通过（L3 通过）。唯一剩余：real_local_sample——LM Studio 已关闭，但模型监督器（pid 23208，进程对设备沙箱不可见）在 77 秒租约内从未登记 paused_for_video；当前代码在该情况下会立即登记，两次验收表现一致，判断为监督器进程仍在运行更新前加载的旧代码（推断，未直接证实）。处理：重启计划任务 MuseLocalModelSupervisor 后重跑。
- 本次无代码改动。

## [B391-P2-35] scripts\b391_verify.py
- 时间：2026-11-27 19:45
- 问题：样片因旧版模型监督器不让出 GPU 而超时时，报告只显示通用错误。
- 修复：识别该错误并在报告中写明原因与监督器 PID、处理办法（结束该进程后重启监督器）；不改变任何 GPU/模型行为。
- SHA-256：`d287117f90e6ffae…` → `ddbb6554ff85951f…`（改前版本 `.p2d.orig`）

### 真机验收第 4 次
- 全量 3,048/3,048 再次全绿（L3 稳定通过）。样片仍以同一原因失败：模型监督器 supervisor_id 53bbab34e14e / PID 23208 自 13:48 起从未变化（未被重启）。已查证旧版 model_supervisor.py（.muse-backups/video_factory/2026-11-25 两份）只在 owner_kind == "video_generation" 时让出 GPU，本地样片用的是 video_encoding 租约 → 被忽略，与 4 次现象完全吻合。根因定为：运行中的监督器是旧代码进程（已证实，非推断）。

## [B391-P2-36] src\muse\remotion_motion.py
- 时间：2026-11-27 20:13
- 问题：5 处 Remotion/FFmpeg 失败信息只截取输出末尾（[-1000:]/[-1200:]），Node/FFmpeg 写在开头的真正错误行被丢掉，且夹带终端颜色码。
- 修复：新增 `_failure_detail`：保留开头 1,500 + 末尾 1,500 字符、去掉 ANSI 颜色码；只改错误信息，不改任何渲染逻辑（Remotion 失败仍硬失败）。
- 回归：`RemotionFailureDetailTests`（2 例）；云端 hardening 25/25、test_remotion_motion 7/7。
- SHA-256：`afe3e34827f94327…` → `2cfbafc8ac6f8dcf…`（原件 `.qw-backups/20261127-b391-p2/src/muse/remotion_motion.py.orig`）

### 真机验收第 5 次
- 真机验收第 5 次（19:56–20:07）：L3 第 3 次全绿（全量 3,048/3,048）。旧监督器已结束（心跳停止），样片 5 秒内拿到 GPU（P2-35 判断得到证实），进入 Remotion 阶段后失败：Remotion 在渲染时调用其自带 ffprobe 读取素材音轨（getAudioChannelsAndDuration）出错；旧错误截取只保留末尾 1,200 字符，根因行被截掉，无法定位。
- Git 取证（只读 .git）：当前分支 b391-full-fusion（2852be1），但 Muse-GitHubSync.ps1 每天提交到当前分支却执行 `git push origin main`——今天 03:00 的“PUSH OK (97 files)”实际只推送了未变化的 main（88250db），B391 分支从未推到 GitHub（远端无 origin/b391-full-fusion）。
