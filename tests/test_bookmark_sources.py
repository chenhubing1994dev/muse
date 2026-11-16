from __future__ import annotations
import json, os, tempfile, unittest
from pathlib import Path
from muse.bookmark_sources import load_creator_bookmarks, BookmarkFirstSearchAdapter

class FakeSearch:
 def search(self, query, *, limit=8):
  return [{"title":"Independent research", "url":"https://example.org/paper", "snippet":"counter evidence", "engine":"fake"}]

class ChromeBookmarkSourceTests(unittest.TestCase):
 def test_reads_only_curated_interest_bookmarks_and_skips_local_urls(self):
  payload={"roots":{"bookmark_bar":{"type":"folder","name":"书签栏","children":[
   {"type":"folder","name":"玄学","children":[
    {"type":"url","name":"易经研究","url":"https://example.org/yijing"},
    {"type":"url","name":"本地命盘","url":"http://localhost:3000/"},
    {"type":"url","name":"成人内容","url":"https://example.org/porn"}]},
   {"type":"folder","name":"IT","children":[{"type":"url","name":"普通博客","url":"https://example.org/blog"}]}
  ]}}}
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/"Bookmarks.json"; path.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
   old=os.environ.get("MUSE_CHROME_BOOKMARKS_FILE"); os.environ["MUSE_CHROME_BOOKMARKS_FILE"]=str(path)
   try:
    rows=load_creator_bookmarks()
   finally:
    if old is None: os.environ.pop("MUSE_CHROME_BOOKMARKS_FILE",None)
    else: os.environ["MUSE_CHROME_BOOKMARKS_FILE"]=old
  self.assertEqual([row["url"] for row in rows],["https://example.org/yijing"])
 def test_bookmark_first_adapter_adds_curated_sources_only_to_support_query(self):
  bookmarks=[{"title":"易经","url":"https://example.org/yijing","folder":"玄学","host":"example.org"}]
  adapter=BookmarkFirstSearchAdapter(FakeSearch(),bookmarks,bookmark_query="support")
  support=adapter.search("support",limit=4)
  counter=adapter.search("counter",limit=4)
  self.assertEqual(support[0]["source_kind"],"creator_chrome_bookmark")
  self.assertEqual(len(counter),1)
  self.assertEqual(counter[0]["url"],"https://example.org/paper")

 def test_github_bookmark_uses_readable_raw_readme_mirror(self):
  payload={"roots":{"bookmark_bar":{"type":"folder","name":"书签栏","children":[
   {"type":"folder","name":"玄学","children":[{"type":"url","name":"紫微斗数开源引擎","url":"https://github.com/Renhuai123/ziwei-doushu"}]}
  ]}}}
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/"Bookmarks.json"; path.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
   old=os.environ.get("MUSE_CHROME_BOOKMARKS_FILE"); os.environ["MUSE_CHROME_BOOKMARKS_FILE"]=str(path)
   try: rows=load_creator_bookmarks()
   finally:
    if old is None: os.environ.pop("MUSE_CHROME_BOOKMARKS_FILE",None)
    else: os.environ["MUSE_CHROME_BOOKMARKS_FILE"]=old
  self.assertEqual(rows[0]["url"],"https://github.com/Renhuai123/ziwei-doushu")
  self.assertEqual(rows[0]["read_url"],"https://raw.githubusercontent.com/Renhuai123/ziwei-doushu/main/README.md")
 def test_counter_reference_sources_are_added_only_to_counter_query(self):
  counter_source={"title":"Nature double blind study","url":"https://www.nature.com/articles/318419a0","snippet":"Double blind tests","source_kind":"peer_reviewed_research_article"}
  adapter=BookmarkFirstSearchAdapter(FakeSearch(),[],bookmark_query="support",counter_query="counter",counter_sources=[counter_source])
  counter=adapter.search("counter",limit=4)
  support=adapter.search("support",limit=4)
  self.assertEqual(counter[0]["url"],counter_source["url"])
  self.assertEqual(support[0]["url"],"https://example.org/paper")
if __name__=="__main__": unittest.main()
