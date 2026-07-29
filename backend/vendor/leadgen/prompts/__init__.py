"""Prompt builders for the Lead Gen Agent.

Two families, kept apart because they target two different models:

  * reasoning  — strings fed to the HF chat LLM (llm.py): ICP synthesis, lead
                 qualification, and the follow-up *decision*. These ask for JSON.
  * outreach   — the freeform `instruction` string handed to the app's Email Writer
                 Space, which writes the actual email. These are the "custom prompts
                 for the email generator" that steer a marketing-email model toward a
                 personalized 1:1 outreach register.
"""

from . import outreach, reasoning

__all__ = ["reasoning", "outreach"]
