# Obsidian 長期記憶整合研究

更新：2026-11-02

## 目標

讓 Muse 的長期記憶以普通 Markdown 文件呈現，使用者可直接閱讀、備份、搜尋及在 Obsidian 中建立關聯；同時保留 Muse 現有 SQLite 候選審批、來源追溯、修訂歷史與運行審計能力。

## 候選方案與決策

### 1. Obsidian 官方 CLI（当前已采用）
- 官方文件：<https://obsidian.md/cli>、<https://obsidian.md/help/cli>
- 可用命令包括 search、search:context、read、create、append、tags、properties、diff 等。
- 本机实测 Obsidian 1.14.5（installer 1.14.5）；官方 `Obsidian.com` CLI 已启用。CLI 要求 Obsidian app 运行，并可通过 `vault=<name>` 精确指定目标 Vault。
- Muse 已验证：`version`、`vault info=path`、`vault info=files`、指定 Vault 的 `search`、`search:context` 和 `read`；目标 Vault `<MUSE_ROOT>\\vault`，名称 `vault`。
- 决策：优先使用官方 CLI，当前不安装任何社区插件、不启动额外服务。

### 2. Local REST API with MCP（首選 Agent bridge）
- 上游：<https://github.com/coddingtonbear/obsidian-local-rest-api>
- Obsidian Community：<https://community.obsidian.md/plugins/obsidian-local-rest-api>
- API 文件：<https://coddingtonbear.github.io/obsidian-local-rest-api/>
- 上游 README 標示 MIT license；Community listing 當前檢索版本 5.5.2、Desktop only。
- 內置 REST + MCP，支援 Vault CRUD、精準 patch、全文/metadata search、事件流及打開筆記；HTTPS + API key 認證。
- 決策：優先採用插件內置 MCP/REST，不再另建同類 MCP server。安裝前需核對當時版本、API key 存放、localhost bind、TLS 與實際暴露工具。

### 3. REST and MCP server（備選）
- Community：<https://community.obsidian.md/plugins/cli-rest-mcp>
- MIT；預設 localhost、API key 認證、危險命令預設關閉。
- 功能有重疊；只有 Local REST API 不兼容或維護狀態改變時再比較，不同時安裝兩套重複服務。

### 4. MemVault（暫不採用）
- Community：<https://community.obsidian.md/plugins/memvault>
- 可同步 Agent memory 並提供 Obsidian 面板，但需要獨立 MemVault REST backend。
- Muse 已有 SQLite MemoryStore、候選審批與檢索；再引入另一個 memory backend 會產生雙重真相與額外服務維護成本，因此暫不採用。若未來出現清晰能力缺口，再按 license/security/maintenance 重評。

## Muse 第一階段實作

- `src/muse/obsidian_vault.py` 將有效、已批准的 SQLite 記憶輸出成普通 Markdown；YAML frontmatter 保存 muse_id、project、source、confidence、revision、created_at、updated_at 和 tags。
- Vault 使用 `Muse Memory Index.md` 與 Obsidian wikilinks；按 Profile / Projects / Knowledge / Decisions 分類。
- `muse-memory export-obsidian --vault <path>` 可重複執行；既有筆記不覆蓋、已刪除記憶不自動刪除文件。
- `src/muse/obsidian_cli.py` 封裝官方 CLI，只提供 version、Vault path、search 和 read；不使用 shell，搜尋結果 JSON 化，筆記路徑拒絕絕對路徑/目錄穿越。`muse-memory obsidian-search` / `obsidian-read` 可直接調用。
- 無新增 Python 第三方依賴；SQLite 仍是權威來源，Vault 是安全單向可讀鏡像。雙向編輯同步須先有修改檢測、衝突報告、人工合併和回滾測試，才可轉換權威來源。

## 安全及後續驗收

1. 不將 API key 寫入 Vault、配置樣例、日誌或同步文件。
2. 插件服務預設只綁定 localhost；不向 LAN/互聯網暴露，除非另行批准。
3. 私人對話與個人資料只在本機模型/本機 Vault 處理；不傳送至第三方記憶服務。
4. 先完成 Obsidian 安裝/版本檢查，再驗證 CLI list/search/read；之後才開放 MCP 只讀工具。
5. 寫入須遵守 Muse 記憶審批流程；刪除/修改須有 revision/conflict guard、審計與備份。
6. 上游版本、license、依賴與安全狀態需在每次升級時重新核對，不能永久沿用本次研究結論。
