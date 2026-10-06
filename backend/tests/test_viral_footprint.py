"""Media-level regression tests for Viral Footprint."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.viral_footprint import router
from app.services.viral_footprint import jobs, media, public_search, scoring


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class ViralFootprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.base = cls.folder / "base.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=128x192:rate=12", "-t", "3", "-c:v", "mpeg4", "-y", str(cls.base)], check=True)
        cls.recompressed = cls.folder / "recompressed.mp4"
        cls.cropped = cls.folder / "cropped.mp4"
        cls.shortened = cls.folder / "shortened.mp4"
        cls.unrelated = cls.folder / "unrelated.mp4"
        cls.subtitled = cls.folder / "subtitled.mp4"
        for source, output, filters, start, length in [
            (cls.base, cls.recompressed, "scale=96:144", "0", "3"),
            (cls.base, cls.cropped, "crop=112:168:8:12,scale=128:192", "0", "3"),
            (cls.base, cls.shortened, "null", "0.2", "2.7"),
            (cls.base, cls.subtitled, "drawbox=x=0:y=160:w=128:h=16:color=black:t=fill", "0", "3"),
        ]:
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", start, "-i", str(source),
                            "-t", length, "-vf", filters, "-c:v", "mpeg4", "-q:v", "4", "-y", str(output)], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=white:s=128x192:r=12", "-t", "3", "-c:v", "mpeg4", "-y", str(cls.unrelated)], check=True)
        cls.fps = {name: media.fingerprint(getattr(cls, name)) for name in
                   ("base", "recompressed", "cropped", "shortened", "subtitled", "unrelated")}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_media_and_related_matches(self) -> None:
        info = media.probe(self.base)
        self.assertEqual(info["width"], 128)
        self.assertEqual(info["height"], 192)
        self.assertGreater(info["frame_count"], 0)
        exact = scoring.score_match(self.fps["base"], self.fps["base"])
        self.assertEqual(exact["similarity"], 100)
        self.assertTrue(exact["exact"])
        unrelated = scoring.score_match(self.fps["base"], self.fps["unrelated"])
        self.assertFalse(unrelated["is_match"])
        for name in ("recompressed", "cropped", "shortened", "subtitled"):
            related = scoring.score_match(self.fps["base"], self.fps[name])
            self.assertGreater(related["similarity"], unrelated["similarity"] + 12, name)
            self.assertTrue(related["is_match"], name)

    def test_thresholds_transcript_and_virality(self) -> None:
        base = self.fps["base"]
        changed = dict(base, exact_file_hash="other", transcript="same spoken words")
        original = dict(base, transcript="same spoken words")
        self.assertEqual(scoring.transcript_similarity("A cat runs", "cat runs fast"), .5)
        self.assertTrue(scoring.score_match(original, changed)["is_match"])
        strict = scoring.MatchConfig(possible=1.01, strong=1.01, certain=1.01)
        self.assertFalse(scoring.score_match(original, changed, strict)["is_match"])
        summary = scoring.content_virality([{"platform": "youtube", "views": 1000},
                                            {"platform": "tiktok", "views": 2000}])
        self.assertEqual(summary["observed_views"], 3000)
        self.assertEqual(summary["detected_copies"], 2)
        self.assertFalse(summary["views_are_unique_people"])
        self.assertIsNone(scoring.content_virality([])["score"])
        self.assertIsNone(scoring.content_virality([])["observed_views"])
        self.assertIsNone(scoring.content_virality([{"platform": "youtube", "views": None}])["score"])
        self.assertGreater(summary["score"], 0)
        # A matching title and duration do not override unrelated footage.
        unrelated = dict(self.fps["unrelated"], transcript="same spoken words")
        self.assertFalse(scoring.score_match(original, unrelated)["is_match"])
        self.assertEqual(public_search.query_terms("  dancing  cat  ", ""), "dancing cat")
        with self.assertRaises(public_search.PublicSearchUnavailable):
            public_search.query_terms("", "")

    def test_candidate_retrieval_keeps_shorter_edits_and_topic_references(self) -> None:
        rows = [{"video_id": str(index), "duration_s": duration, "views": index * 1000}
                for index, duration in enumerate((20, 45, 90, 108, 140, 175, 1200), 1)]
        selected, eligible = public_search.select_candidates(rows, 108)
        self.assertEqual(len(selected), 6)
        self.assertEqual({row["duration_s"] for row in eligible}, {20, 45, 90, 108, 140, 175})
        self.assertEqual(selected[0]["duration_s"], 108)
        benchmark = scoring.comparison_benchmark(eligible)
        self.assertEqual(benchmark["median_views"], 3500)
        with patch.object(public_search.ytsearch, "search", side_effect=[[rows[0]], [rows[0], rows[1]]]) as search:
            found = public_search.search("sci-fi")
        self.assertEqual(len(found), 2)
        self.assertEqual([call.args[0] for call in search.call_args_list], ["sci-fi", "sci-fi"])
        self.assertEqual([call.kwargs["short_videos"] for call in search.call_args_list], [True, False])
        self.assertEqual(public_search.reference_id("https://youtu.be/abc12345678"), "abc12345678")
        with self.assertRaises(ValueError):
            public_search.reference_id("https://127.0.0.1/watch?v=abc12345678")

    def test_upload_validation_persistence_and_failure(self) -> None:
        database = self.folder / "test.sqlite3"
        original_db = config.DB_PATH
        config.DB_PATH = database
        try:
            jobs.initialize()
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            self.assertEqual(client.get("/align/videos/source").json()["platforms"], ["YouTube"])
            row = {"video_id": "abc12345678", "duration_s": 3, "url": "https://www.youtube.com/watch?v=abc12345678",
                   "channel": "Test creator", "title": "Test reel", "views": 2400}
            details = {"title": "Test reel", "view_count": 2400, "channel": "Test creator", "upload_date": "20261001"}
            with patch.object(jobs, "_transcribe", return_value=("", None)), \
                 patch.object(jobs.public_search, "search", return_value=[row]), \
                 patch.object(jobs.public_search, "download_video", return_value=(self.base, details)):
                with self.base.open("rb") as video:
                    response = client.post("/align/videos/analyze", data={"search_terms": "test reel"}, files={"file": ("sample.mp4", video, "video/mp4")})
                self.assertEqual(response.status_code, 202)
                job_id = response.json()["analysis_id"]
                for _ in range(150):
                    state = client.get(f"/align/videos/{job_id}").json()
                    if state["status"] in {"completed", "failed"}:
                        break
                    time.sleep(.1)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(state["status"], "completed", state.get("error"))
            self.assertIsNotNone(state["metadata"])
            self.assertEqual(state["report"]["matches"][0]["views"], 2400)
            self.assertEqual(state["report"]["coverage"]["checked"], 1)
            self.assertEqual(state["report"]["coverage"]["query"], "test reel")
            self.assertGreater(state["report"]["content_virality"]["score"], 0)
            other_row = {**row, "video_id": "other123456", "url": "https://www.youtube.com/watch?v=other123456"}
            with patch.object(jobs, "_transcribe", return_value=("", None)), \
                 patch.object(jobs.public_search, "search", return_value=[other_row]), \
                 patch.object(jobs.public_search, "download_video", return_value=(self.unrelated, details)):
                with self.base.open("rb") as video:
                    unmatched = client.post("/align/videos/analyze", data={"search_terms": "test reel"}, files={"file": ("sample.mp4", video, "video/mp4")})
                for _ in range(150):
                    no_match = client.get(f"/align/videos/{unmatched.json()['analysis_id']}").json()
                    if no_match["status"] in {"completed", "failed"}:
                        break
                    time.sleep(.1)
            self.assertEqual(no_match["status"], "completed")
            self.assertEqual(no_match["report"]["matches"], [])
            self.assertIsNone(no_match["report"]["content_virality"]["score"])
            self.assertEqual(len(no_match["report"]["comparisons"]), 1)
            self.assertEqual(no_match["report"]["benchmark"]["median_views"], 2400)
            legacy_report = {"matches": [], "content_virality": {"score": 0, "observed_views": 0}}
            jobs._set(no_match["id"], "completed", report_json=json.dumps(legacy_report))
            self.assertIsNone(jobs.get(no_match["id"])["report"]["content_virality"]["score"])
            direct_row = {**row, "video_id": "link1234567", "direct_reference": True}
            with patch.object(jobs.public_search, "search") as keyword_search, \
                 patch.object(jobs.public_search, "reference_candidate", return_value=direct_row), \
                 patch.object(jobs.public_search, "download_video", return_value=(self.base, details)):
                with self.base.open("rb") as video:
                    linked = client.post("/align/videos/analyze", data={"reference_url": "https://youtu.be/link1234567"}, files={"file": ("sample.mp4", video, "video/mp4")})
                for _ in range(150):
                    direct_result = client.get(f"/align/videos/{linked.json()['analysis_id']}").json()
                    if direct_result["status"] in {"completed", "failed"}:
                        break
                    time.sleep(.1)
                keyword_search.assert_not_called()
            self.assertEqual(direct_result["status"], "completed", direct_result.get("error"))
            self.assertTrue(direct_result["report"]["matches"][0]["exact"])
            self.assertEqual(direct_result["reference_url"], "https://www.youtube.com/watch?v=link1234567")
            self.assertEqual(client.post("/align/videos/analyze", data={"reference_url": "https://127.0.0.1/clip"}, files={"file": ("bad.mp4", b"x", "video/mp4")}).status_code, 400)
            self.assertEqual(client.post("/align/videos/analyze", files={"file": ("bad.txt", b"x", "text/plain")}).status_code, 400)
            self.assertEqual(client.post("/align/videos/analyze", files={"file": ("empty.mp4", b"", "video/mp4")}).status_code, 400)
            self.assertEqual(client.get("/align/videos/missing").status_code, 404)
            bad = client.post("/align/videos/analyze", files={"file": ("broken.mp4", b"not media", "video/mp4")})
            self.assertEqual(bad.status_code, 202)
            for _ in range(30):
                failed = client.get(f"/align/videos/{bad.json()['analysis_id']}").json()
                if failed["status"] == "failed":
                    break
                time.sleep(.1)
            self.assertEqual(failed["status"], "failed")
        finally:
            config.DB_PATH = original_db

    def test_invalid_media(self) -> None:
        invalid = self.folder / "invalid.mp4"
        invalid.write_bytes(b"not video")
        with self.assertRaises(media.MediaError):
            media.probe(invalid)


if __name__ == "__main__":
    unittest.main()
