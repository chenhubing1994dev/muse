# GitHub 镜像下载策略与实测

日期：2026-11-15

## 镜像顺序

Mingli-AI 公共 Release 下载脚本按以下顺序尝试镜像：

1. `https://ghfast.top/`
2. `https://gh-proxy.com/`
3. `https://ghproxy.net/`
4. `https://githubproxy.cc/`（备用）

所有镜像均只接收公开 GitHub 资源 URL；不传 GitHub Token、私有仓库凭据或签名 URL。Release API 元数据仍从 `api.github.com` 直接读取，不把 API JSON 路由到下载镜像。

## 当前机器实测

对同一公开仓库、同一版本的 raw README、tag archive ZIP、release `merges.txt` 做 64 KiB Range 探测：

| 镜像 | Raw | Archive | Release |
|---|---:|---:|---:|
| ghfast.top | PASS | TIMEOUT（25s，已收到 490,630 bytes） | PASS |
| gh-proxy.com | PASS | TIMEOUT（25s，已收到 508,937 bytes） | PASS |
| ghproxy.net | PASS | PASS | PASS |

总计 7/9 路线探测通过。Archive 的前两家在 25 秒预算内没有完成 Range 请求，不代表服务不可用，但当前环境下不应优先用于 archive。随后对 Release `merges.txt` 完整文件（3,353,259 bytes）进行下载与 SHA-256 核验：ghfast.top、gh-proxy.com 均完整通过；ghproxy.net 在 90 秒内收到 3,111,483 bytes 后超时，未通过完整性核验。TLS 吊销检查在本机网络偶有离线错误，脚本对 curl 使用 `--ssl-no-revoke` 作为 Windows 兼容设置（仍保留 TLS 证书链校验）。

此前单独测试 `githubproxy.cc` 的 Release 资产请求在 90 秒内未完成；本次 3×3 探测不含该站，因此保留为最后备用，不宣称已验证通过。

## 下载安全规则

- 镜像探测只检查传输，不证明完整文件正确。
- 正式下载每个文件仍对照 GitHub Release API 提供的精确文件大小；不符即删除并尝试下一镜像。
- 模型重组后必须继续对照上游 `merged-manifest.json` 的 SHA-256；哈希不符不得启动模型。
- 代理属于第三方中间服务。即使 HTTPS 可用，也不能视为与 GitHub 官方等价的信任边界。
- 不关闭证书验证，不下载私有资源，不把 Token 放入代理 URL。

## 工具

- `scripts/probe_github_mirrors.ps1`：对 Raw / Archive / Release 三种路线做小范围 Range 探测。
- `scripts/download_mingli_release.ps1`：依次尝试镜像，按镜像分别保留 partial、同镜像断点续传，校验 Release 官方文件大小后才落盘；支持缓存已获取的 Release API 清单和 `-PreflightOnly`。54 个资产总计 51.78 GiB，D 盘可用 253.9 GiB，preflight 通过。

本测试不是完整权重下载或模型可运行验收。