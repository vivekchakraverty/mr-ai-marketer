"""One place for the cost and model controls of game analysis."""
import os

MODEL = os.getenv("GAME_ANALYSIS_QWEN_MODEL", "Qwen/Qwen3-VL-2B-Instruct")
GPU = os.getenv("GAME_ANALYSIS_MODAL_GPU", "L4")
MODAL_APP = "mr-ai-marketer-game-analysis"
MODAL_FUNCTION = "observe"
MAX_BYTES = int(os.getenv("GAME_ANALYSIS_MAX_BYTES", str(500 * 1024 * 1024)))
MAX_DURATION = int(os.getenv("GAME_ANALYSIS_MAX_DURATION_SECONDS", "600"))
SAMPLE_FPS = max(0.25, min(4.0, float(os.getenv("GAME_ANALYSIS_SAMPLE_FPS", "1"))))
CHUNK_SECONDS = max(15, min(90, int(os.getenv("GAME_ANALYSIS_CHUNK_SECONDS", "30"))))
CHUNK_OVERLAP = max(0, min(5, int(os.getenv("GAME_ANALYSIS_CHUNK_OVERLAP_SECONDS", "2"))))
MAX_FRAMES = max(4, min(90, int(os.getenv("GAME_ANALYSIS_MAX_FRAMES_PER_CHUNK", "32"))))
MAX_ACTIVE = max(1, min(4, int(os.getenv("GAME_ANALYSIS_MAX_ACTIVE", "1"))))
RETAIN_UPLOADS = os.getenv("GAME_ANALYSIS_RETAIN_UPLOADS", "0") == "1"
SIMILARITY_WEIGHTS = {"mechanics": .35, "genres": .15, "themes": .10,
                      "perspective": .10, "loop": .15, "pace": .05, "audience": .10}
