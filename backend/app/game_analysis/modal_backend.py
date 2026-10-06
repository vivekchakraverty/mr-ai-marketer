"""Deploy from backend/: python -m modal deploy -m app.game_analysis.modal_backend.

The upload is sampled locally. Only bounded JPEG frames reach this private Modal function.
"""
import base64
import json
import sys

import modal
from app.game_analysis import config
from modal._vendor import cloudpickle

# PyInstaller builds have no importable .py source inside the Modal container.
cloudpickle.register_pickle_by_value(sys.modules[__name__])

MODEL = config.MODEL
GPU = config.GPU
app = modal.App(config.MODAL_APP)
volume = modal.Volume.from_name("mr-ai-marketer-qwen-vl-cache", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.11")
         .pip_install("torch", "torchvision", "transformers>=5.0,<6", "accelerate>=1.2", "Pillow",
                      "lm-format-enforcer @ https://github.com/noamgat/lm-format-enforcer/archive/817f944fcc8851917c40300a47f62d1defc3ffc3.zip")
         .env({"HF_HOME": "/models"}))
_loaded = None
_tokenizer_data = None


def _output_schema(synthesis: bool) -> dict:
    """Bound arrays and prose at decoding time so repetition cannot consume the budget."""
    def text(limit=100):
        return {"type": "string", "maxLength": limit}

    def array(item, limit=6):
        return {"type": "array", "items": item, "maxItems": limit}

    def record(fields, required=True):
        return {"type": "object", "properties": fields,
                "required": list(fields) if required else [], "additionalProperties": False}

    number = {"type": "number"}
    times = array(number, 2)
    if synthesis:
        return record({"description": text(1800), "audience_archetypes": array(record({
            "archetype": text(60), "affinity": number, "reason": text(240), "evidence_timestamps": times})),
            "audience_summary": text(800)})
    fields = {
        "description": text(800),
        "observations": array(record({"timestamp": number, "observation": text(180), "confidence": number})),
        "events": array(record({"start": number, "end": number, "type": text(40),
                                "description": text(180), "confidence": number}), 4),
        "observed_actions": array(text()),
        "inferred_mechanics": array(record({"name": text(), "confidence": number, "evidence_timestamps": times}), 4),
        "claims": array(record({"claim": text(180), "category": {"enum": ["strongly_inferred", "tentative"]},
                                "confidence": number, "evidence_timestamps": times}), 4),
    }
    for key in ("genres", "subgenres", "themes", "keywords", "player_perspective", "game_modes", "core_loop",
                "accessibility_observations", "uncertainties"):
        fields[key] = array(text())
    fields.update({"pace": text(40), "complexity": text(40)})
    fields["presentation"] = record({"visual_style": text(120), "ui_density": text(120)}, required=False)
    for key in ("skill_profile", "social_structure", "progression", "economy", "combat", "exploration",
                "construction", "management", "narrative"):
        fields[key] = record({"observed": text(180)}, required=False)
    dimensions = ("action_intensity", "strategic_depth", "creative_expression", "mechanical_difficulty",
                  "management_depth", "narrative_emphasis", "exploration_emphasis", "social_emphasis",
                  "competitive_emphasis", "cooperative_potential", "systemic_emergence", "session_commitment",
                  "learning_curve", "spectator_readability")
    fields["dimensions"] = record({key: record({"score": number, "confidence": number,
                                               "evidence_timestamps": times}) for key in dimensions}, required=False)
    return record(fields)


@app.function(image=image, gpu=GPU, volumes={"/models": volume}, timeout=600,
              scaledown_window=300, serialized=True, include_source=False)
def observe(prompt: str, frames: list[tuple[float, bytes]]) -> str:
    global _loaded, _tokenizer_data
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    if _loaded is None:
        processor = AutoProcessor.from_pretrained(MODEL)
        model = AutoModelForMultimodalLM.from_pretrained(MODEL, dtype="auto", device_map="auto")
        model.eval()
        volume.commit()
        _loaded = (processor, model)
    processor, model = _loaded
    content = [{"type": "text", "text": prompt}]
    for timestamp, raw in frames:
        content.append({"type": "text", "text": f"Frame at {timestamp:.2f} seconds:"})
        content.append({"type": "image", "base64": base64.b64encode(raw).decode("ascii")})
    structured = "json" in prompt.casefold()
    messages = []
    if structured:
        # Frame-by-frame narration can exhaust the output budget before the JSON
        # closes. Summarize representative evidence across the entire chunk.
        messages.append({"role": "system", "content": [{"type": "text", "text":
            "Return one complete, compact JSON object matching the requested schema. "
            "Do not enumerate every frame. Select at most 6 observations covering the beginning, middle and end; "
            "at most 4 events, 4 inferred mechanics, 4 claims, and 6 audience archetypes. "
            "Use at most 6 entries in other lists and 2 timestamps per evidence list. "
            "Keep each observation and event under 25 words, and each description under 120 words. "
            "Include all schema keys, use empty values for unknown details, and finish the JSON object. "
            "No markdown or text outside JSON."}]})
    messages.append({"role": "user", "content": content})
    inputs = processor.apply_chat_template(messages, add_generation_prompt=True,
                                           tokenize=True, return_dict=True, return_tensors="pt").to(model.device)
    generation = {}
    if structured:
        from lmformatenforcer import JsonSchemaParser
        from lmformatenforcer.characterlevelparser import CharacterLevelParserConfig
        from lmformatenforcer.integrations.transformers import (
            build_token_enforcer_tokenizer_data, build_transformers_prefix_allowed_tokens_fn,
        )
        if _tokenizer_data is None:
            _tokenizer_data = build_token_enforcer_tokenizer_data(processor.tokenizer)
        parser = JsonSchemaParser(_output_schema(not frames), config=CharacterLevelParserConfig(
            max_consecutive_whitespaces=2, force_json_field_order=True))
        generation["prefix_allowed_tokens_fn"] = build_transformers_prefix_allowed_tokens_fn(_tokenizer_data, parser)
    with torch.inference_mode():
        # Use Qwen's published sampling settings. Greedy decoding was getting
        # stuck repeating observations until the response budget was exhausted.
        output = model.generate(**inputs, max_new_tokens=4096 if structured else 1800,
                                do_sample=True, temperature=0.7, top_p=0.8, top_k=20, **generation)
    response = processor.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    if structured:
        # Do not send a partial response into the local report pipeline.
        json.loads(response)
    return response
