from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from gradio_client import Client
from pydantic import BaseModel

from .. import config, db

router = APIRouter(prefix="/blog-writer", tags=["blog-writer"])

_SPACE_ID = "vivekchakraverty/BlogWriter"
_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(_SPACE_ID)
    return _client


class GenerateBlogRequest(BaseModel):
    topic: str
    primaryKeyword: str = ""
    secondaryKeyword: str = ""
    brief: str = ""
    targetWordCount: int = 1200
    contentGoal: str = "Informational"
    hfToken: str = ""


class BlogImage(BaseModel):
    url: str
    caption: str | None = None


class GenerateBlogResponse(BaseModel):
    title: str
    markdown: str
    images: list[BlogImage]
    docxPath: str | None
    docxUrl: str | None
    libraryId: str


def _outputs_url(path: Path) -> str:
    return "/outputs/" + path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix()


@router.post("/generate", response_model=GenerateBlogResponse)
def generate_blog(body: GenerateBlogRequest) -> GenerateBlogResponse:
    if not body.topic.strip():
        raise HTTPException(status_code=400, detail="Topic is required.")
    if not body.hfToken.strip():
        raise HTTPException(status_code=400, detail="Please connect your Hugging Face account.")

    try:
        client = _get_client()
        progress, preview, gallery, docx_file = client.predict(
            topic=body.topic,
            primary=body.primaryKeyword,
            secondary=body.secondaryKeyword,
            brief=body.brief,
            target_wordcount=body.targetWordCount,
            content_goal=body.contentGoal,
            hf_token=body.hfToken,
            api_name="/generate",
        )
    except Exception as err:  # noqa: BLE001 — surface Space/network failures as a clean API error
        raise HTTPException(status_code=502, detail=f"Blog generation failed: {err}") from err

    if isinstance(progress, str) and progress.lower().startswith(("error", "please")):
        raise HTTPException(status_code=502, detail=progress)

    # gradio_client downloads file/image outputs into its own temp dir, which isn't
    # guaranteed to outlive this process — copy anything we want to keep into our own
    # per-run output directory before returning.
    run_dir = config.OUTPUTS_DIR / "blog" / str(uuid.uuid4())
    run_dir.mkdir(parents=True, exist_ok=True)

    images: list[BlogImage] = []
    for i, item in enumerate(gallery or []):
        img = item.get("image") if isinstance(item, dict) else None
        src_path = img.get("path") if isinstance(img, dict) else None
        caption = item.get("caption") if isinstance(item, dict) else None
        if not src_path:
            continue
        dest = run_dir / f"image_{i + 1}{Path(src_path).suffix or '.png'}"
        shutil.copy(src_path, dest)
        images.append(BlogImage(url=_outputs_url(dest), caption=caption))

    docx_path: str | None = None
    docx_url: str | None = None
    if docx_file:
        src = docx_file if isinstance(docx_file, str) else docx_file.get("path")
        if src and Path(src).exists():
            dest = run_dir / "blog.docx"
            shutil.copy(src, dest)
            docx_path = str(dest)
            docx_url = _outputs_url(dest)

    item = db.add_item(tool="Blog", title=body.topic, subtitle="Blog article", content=preview, output_path=docx_path)
    return GenerateBlogResponse(
        title=body.topic, markdown=preview, images=images, docxPath=docx_path, docxUrl=docx_url, libraryId=item["id"]
    )
