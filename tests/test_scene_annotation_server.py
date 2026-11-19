from __future__ import annotations
import csv, json, tempfile, threading, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
import tools.scene_annotation_server as app
from tools.scene_annotation_server import FIELDS, load_samples, save_annotation, validate_annotation

class AnnotationServerTests(unittest.TestCase):
    def dataset(self, root):
        d=root/"b160"; (d/"frames").mkdir(parents=True); (d/"frames"/"f.jpg").write_bytes(b"jpeg")
        with (d/"annotations.csv").open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerow({"sample_id":"s-1","frame_path":"frames/f.jpg","pred_presence":"present","pred_source":"baseline","pred_camera_motion":"high"})
        return d

    def test_invalid_label_and_box_rejected(self):
        base={"presence":"present","camera_motion":"low","annotator":"tester"}
        with self.assertRaises(ValueError): validate_annotation({**base,"presence":"maybe"})
        with self.assertRaises(ValueError): validate_annotation({**base,"x_min":"0.8","y_min":"0.2","x_max":"0.1","y_max":"0.9"})
    def test_save_preserves_model_prediction_columns(self):
        with tempfile.TemporaryDirectory() as t:
            d=self.dataset(Path(t)); save_annotation("s-1",{"presence":"present","camera_motion":"medium","annotator":"human","x_min":".1","y_min":".2","x_max":".7","y_max":".8"},d)
            row=load_samples(d)[0]; self.assertEqual(row["presence"],"present"); self.assertEqual(row["pred_presence"],"present"); self.assertEqual(row["pred_source"],"baseline"); self.assertEqual(row["pred_camera_motion"],"high")

    def test_unknown_id_and_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            d=self.dataset(Path(t))
            with self.assertRaises(KeyError): save_annotation("unknown",{"presence":"absent","camera_motion":"static","annotator":"human"},d)
            with (d/"annotations.csv").open("w",newline="",encoding="utf-8") as f:
                w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerow({"sample_id":"s-1","frame_path":"../secret"})
            with self.assertRaises(ValueError): load_samples(d)
    def test_http_page_and_save_endpoint(self):
        with tempfile.TemporaryDirectory() as t:
            old=app.DATASET; app.DATASET=self.dataset(Path(t))
            server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                base=f"http://127.0.0.1:{server.server_port}"
                page = urlopen(base+"/").read().decode()
                self.assertIn("人工视觉标注台", page)
                self.assertIn("模型预测（只读，不作为人工真值）", page)
                sample=json.loads(urlopen(base+"/api/samples").read())["samples"][0]
                self.assertEqual(sample["sample_id"],"s-1")
                req=Request(base+"/api/annotation/s-1",data=json.dumps({"presence":"absent","camera_motion":"static","annotator":"http-test"}).encode(),headers={"Content-Type":"application/json"},method="POST")
                saved=json.loads(urlopen(req).read())["sample"]
                self.assertEqual(saved["presence"],"absent"); self.assertEqual(saved["pred_source"],"baseline")
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2); app.DATASET=old

    def test_concurrent_http_saves_do_not_lose_other_annotations(self):
        with tempfile.TemporaryDirectory() as t:
            d = self.dataset(Path(t))
            (d / "frames" / "g.jpg").write_bytes(b"jpeg")
            with (d / "annotations.csv").open("a", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=FIELDS).writerow({"sample_id": "s-2", "frame_path": "frames/g.jpg", "pred_source": "second"})
            old = app.DATASET
            app.DATASET = d
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            def submit(sample_id, label):
                try:
                    payload = {"presence": label, "camera_motion": "low", "annotator": "parallel-test"}
                    req = Request(f"http://127.0.0.1:{server.server_port}/api/annotation/{sample_id}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
                    urlopen(req, timeout=5).read()
                except Exception as exc:
                    errors.append(exc)
            workers = [threading.Thread(target=submit, args=("s-1", "present")), threading.Thread(target=submit, args=("s-2", "absent"))]
            try:
                for worker in workers: worker.start()
                for worker in workers: worker.join(timeout=8)
                self.assertFalse(errors, errors)
                rows = {row["sample_id"]: row for row in load_samples(d)}
                self.assertEqual(rows["s-1"]["presence"], "present")
                self.assertEqual(rows["s-2"]["presence"], "absent")
                self.assertEqual(rows["s-2"]["pred_source"], "second")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                app.DATASET = old

if __name__=="__main__": unittest.main()
