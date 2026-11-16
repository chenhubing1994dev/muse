"""Interest-led Chrome bookmark discovery for Muse research.
Only reads Chrome's bookmark JSON files. Never reads history, cookies, passwords,
or browsing sessions. Bookmark contents are untrusted discovery hints, not evidence.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path
from urllib.parse import urlsplit

INTEREST_TERMS = re.compile(
    r"易经|周易|八字|紫微|奇门|六壬|风水|玄学|占卜|命理|星盘|占星|塔罗|道教|佛学|国学|术数|阴阳|五行|梅花|遁甲|择日|堪舆|astrolog|metaphys|divination|feng.?shui|i.?ching|tarot|bazi|ziwei|qimen|liuren|daoism",
    re.I,
)
FOLDER_HINTS = ("玄学", "文化/神秘", "文化")
MAX_BOOKMARKS = 24
EXCLUDE_TERMS = re.compile(r"情色|香艳|成人|porn|xxx|sex", re.I)

def _candidate_files() -> list[Path]:
    explicit = os.environ.get("MUSE_CHROME_BOOKMARKS_FILE", "").strip()
    if explicit:
        return [Path(explicit)]
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        user = os.environ.get("USERPROFILE")
        local = str(Path(user) / "AppData/Local") if user else ""
    if not local:
        return []
    root = Path(local) / "Google/Chrome/User Data"
    files = []
    # Default is the profile containing the creator's explicitly curated metaphysics folder.
    for profile in ("Default", "Profile 1", "Profile 2"):
        directory = root / profile
        files.extend(directory / name for name in ("AccountBookmarks", "Bookmarks", "Bookmarks.bak"))
    return files

def load_creator_bookmarks(*, limit: int = MAX_BOOKMARKS) -> list[dict]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    source = next((path for path in _candidate_files() if path.is_file()), None)
    if source is None:
        return []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    rows = []
    seen = set()
    def visit(node, folder=""):
        if not isinstance(node, dict):
            return
        kind = node.get("type")
        name = str(node.get("name", ""))[:300]
        current = (folder + "/" + name).strip("/") if kind == "folder" else folder
        if kind == "url":
            title = name
            url = node.get("url", "")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                parsed = urlsplit(url)
                host = (parsed.hostname or "").casefold()
                read_url = url
                if host == "github.com":
                    segments = [part for part in parsed.path.split("/") if part]
                    if len(segments) >= 2:
                        read_url = f"https://raw.githubusercontent.com/{segments[0]}/{segments[1]}/main/README.md"
                if (host and host not in {"localhost", "127.0.0.1", "::1"}
                    and INTEREST_TERMS.search(title + " " + url + " " + current)
                    and not EXCLUDE_TERMS.search(title + " " + url)):
                    key = url.split("#", 1)[0].rstrip("/")
                    if key not in seen:
                        seen.add(key)
                        rows.append({"title": title, "url": url, "read_url": read_url, "folder": current, "host": host})
        for child in node.get("children", []):
            visit(child, current)
    for key, root in data.get("roots", {}).items():
        visit(root, key)
    # Explicitly curated folder first; then related classics/knowledge folders.
    preferred_urls = {
        "https://github.com/Renhuai123/ziwei-doushu": 0,
        "https://qimen.live/": 1,
        "https://yijing.5000yan.com/": 2,
        "https://xx.theojs.cn/": 3,
        "https://www.xuanhuwenhua.cn/": 4,
        "https://www.fateqi.com/": 5,
    }
    rows.sort(key=lambda item: (
        preferred_urls.get(item["url"].rstrip("/") + ("/" if item["url"].endswith("/") else ""), 20),
        0 if "/玄学" in item["folder"] else
        1 if "/文化/神秘" in item["folder"] else
        2 if "/文化" in item["folder"] else 3,
        item["host"], item["title"].casefold(),
    ))
    return rows[:limit]

class BookmarkFirstSearchAdapter:
    """Adds creator bookmarks and explicit reference sources to relevant search angles."""
    def __init__(self, delegate, bookmarks, *, bookmark_query: str,
                 counter_query: str = "", support_sources=(), counter_sources=()):
        self.delegate = delegate
        self.bookmarks = list(bookmarks)
        self.bookmark_query = bookmark_query
        self.counter_query = counter_query
        self.support_sources = list(support_sources)
        self.counter_sources = list(counter_sources)

    def search(self, query: str, *, limit: int = 8):
        base = list(self.delegate.search(query, limit=limit))
        selected = []
        if query == self.bookmark_query:
            for item in self.bookmarks:
                selected.append({
                    "title": item["title"],
                    "url": item.get("read_url") or item["url"],
                    "bookmark_url": item["url"],
                    "snippet": "Creator Chrome bookmark; curated interest source. Original bookmark: " + item["url"] + ". Folder: " + item["folder"],
                    "source_kind": "creator_chrome_bookmark",
                    "engine": "chrome_bookmark_import",
                })
            selected.extend(self.support_sources)
        elif self.counter_query and query == self.counter_query:
            selected.extend(self.counter_sources)
        merged = selected + base
        seen = set()
        unique = []
        for item in merged:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(item)
        return unique[:limit]
