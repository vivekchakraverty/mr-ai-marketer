"""BrandForge brand-document generation, ported from the BrandForge Space's
src/* for the mr-ai-marketer backend.

Only the pieces needed at *inference* time live here — no RAG index, no
embeddings, no book corpus. The branding-book knowledge is baked into the
fine-tuned Qwen3-8B LoRA weights,
so a plain prompt reproduces the teacher's grounding without retrieval.

The prompt format (sections.build_student_messages) is copied verbatim from
the fine-tune's training-time student prompt (finetune/teacher_generate.py in
the BrandForge repo) so inference matches training exactly.
"""
