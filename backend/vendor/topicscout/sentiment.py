"""Sentiment over the evidence behind each ranked topic.

TrendScope's own layering is preserved — a real model first, a bilingual keyword
engine as the always-available floor — but the model tier is served over Hugging
Face Inference Providers instead of a local pysentimiento/PyTorch install. That is
the same billing path every other generator in this app uses (the user's own HF
token, per request), and it avoids shipping torch into a PyInstaller bundle where
its DLLs are frequently blocked anyway.

Only evidence attached to a topic that made the final ranking is analyzed. Running
inference over every discovered headline would multiply cost by roughly ten for
results nobody sees.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .models import Evidence, SentimentProfile, Topic

DEFAULT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"

# Different sentiment checkpoints name the same three classes differently. Rather
# than pin one model forever, normalise whatever comes back.
_LABEL_ALIASES = {
    "positive": "positive", "pos": "positive", "label_2": "positive", "5 stars": "positive", "4 stars": "positive",
    "negative": "negative", "neg": "negative", "label_0": "negative", "1 star": "negative", "2 stars": "negative",
    "neutral": "neutral", "neu": "neutral", "label_1": "neutral", "3 stars": "neutral",
}

# TrendScope's bilingual keyword lexicon, used verbatim as the offline floor.
_POSITIVE = {
    "love", "great", "best", "amazing", "awesome", "excellent", "wonderful",
    "fantastic", "good", "perfect", "brilliant", "outstanding", "superb",
    "incredible", "beautiful", "success", "win", "winning", "growth",
    "breakthrough", "innovative", "revolutionary", "impressive", "top",
    "trending", "popular", "viral", "boom", "record", "achievement",
    "opportunity", "benefit", "positive", "optimistic", "hope", "excited",
    "excelente", "increible", "genial", "bueno", "mejor", "perfecto",
    "encanta", "recomiendo", "fantastico", "maravilloso", "feliz", "gran",
    "innovador", "revolucionario", "impresionante", "hermoso", "brillante",
    "logro", "exito", "victoria", "avance", "progreso", "crecimiento",
    "oportunidad", "beneficio", "positivo", "optimista", "esperanza",
}

_NEGATIVE = {
    "bad", "worst", "hate", "terrible", "horrible", "awful", "poor",
    "scam", "fraud", "disappointing", "broken", "crash", "fail", "failure",
    "fear", "danger", "warning", "alert", "crisis", "collapse", "dead",
    "death", "war", "violence", "panic", "loss", "debt", "bankruptcy",
    "corruption", "disaster", "threat", "risk", "decline", "recession",
    "layoff", "fired", "scandal", "controversy", "outrage", "angry",
    "malo", "peor", "estafa", "odio", "decepcionante", "basura", "inutil",
    "caro", "fallo", "error", "problema", "colapso", "caida", "desastre",
    "peligro", "muerto", "muerte", "guerra", "miedo", "panico",
    "rechazo", "fracaso", "perdida", "deuda", "quiebra", "corrupcion",
}

# Enough inference to be representative without turning one report into hundreds of
# billed requests.
MAX_TEXTS = 180
_MAX_WORKERS = 8


def _normalise(label: str) -> str:
    return _LABEL_ALIASES.get((label or "").strip().lower(), "neutral")


def _keyword_sentiment(text: str) -> tuple[str, float]:
    words = set(text.lower().replace(",", " ").replace(".", " ").split())
    positive = len(words & _POSITIVE)
    negative = len(words & _NEGATIVE)
    if positive > negative:
        return "positive", min(0.95, 0.6 + positive * 0.1)
    if negative > positive:
        return "negative", min(0.95, 0.6 + negative * 0.1)
    return "neutral", 0.5


def _classify_all(texts: list[str], hf_token: str, model: str) -> tuple[dict[str, tuple[str, float]], str, str]:
    """Label every text. Returns (labels by text, engine name, error note)."""
    if not hf_token.strip():
        return (
            {text: _keyword_sentiment(text) for text in texts},
            "keyword fallback",
            "No Hugging Face token configured, so tone was estimated with the keyword engine.",
        )

    from huggingface_hub import InferenceClient  # noqa: PLC0415 — heavy import, tool-local

    client = InferenceClient(api_key=hf_token, timeout=30)

    def classify(text: str) -> tuple[str, float]:
        predictions = client.text_classification(text[:512], model=model)
        top = max(predictions, key=lambda item: item.score)
        return _normalise(top.label), round(float(top.score), 4)

    # Probe once before fanning out: a model that is not served by any provider
    # should cost one failed request, not one per headline.
    try:
        first = classify(texts[0])
    except Exception as err:  # noqa: BLE001 — any inference failure means fall back
        return (
            {text: _keyword_sentiment(text) for text in texts},
            "keyword fallback",
            f"Hugging Face sentiment was unavailable ({type(err).__name__}), so tone was estimated with the keyword engine.",
        )

    labels: dict[str, tuple[str, float]] = {texts[0]: first}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        rest = texts[1:]
        for text, result in zip(rest, pool.map(lambda t: _safe_classify(classify, t), rest)):
            labels[text] = result
    return labels, model, ""


def _safe_classify(classify, text: str) -> tuple[str, float]:
    """One flaky request must not lose the whole batch."""
    try:
        return classify(text)
    except Exception:  # noqa: BLE001
        return _keyword_sentiment(text)


def _profile(evidence: list[Evidence], engine: str) -> SentimentProfile:
    labelled = [item for item in evidence if item.sentiment_label]
    if not labelled:
        return SentimentProfile(engine=engine)

    counts = Counter(item.sentiment_label for item in labelled)
    # Polarity weights each item by the model's own confidence, so a wall of
    # barely-positive headlines does not read the same as genuine enthusiasm.
    polarity = sum(
        item.sentiment_score if item.sentiment_label == "positive"
        else -item.sentiment_score if item.sentiment_label == "negative"
        else 0.0
        for item in labelled
    ) / len(labelled)

    # A dead heat between positive and negative is not "positive because it was
    # counted first" — it is a topic people disagree about, which is worth saying.
    positive, negative = counts.get("positive", 0), counts.get("negative", 0)
    label = "mixed" if positive and positive == negative else counts.most_common(1)[0][0]

    return SentimentProfile(
        label=label,
        positive=counts.get("positive", 0),
        negative=counts.get("negative", 0),
        neutral=counts.get("neutral", 0),
        polarity=round(polarity, 3),
        analyzed=len(labelled),
        engine=engine,
    )


def apply_sentiment(topics: list[Topic], hf_token: str, model: str = "") -> str:
    """Tag every ranked topic's evidence with tone and roll it up. Returns a note."""
    if not topics:
        return ""

    seen: dict[str, list[Evidence]] = {}
    for topic in topics:
        for item in topic.evidence:
            text = item.title.strip()
            if text:
                seen.setdefault(text, []).append(item)

    texts = list(seen)[:MAX_TEXTS]
    if not texts:
        return ""

    labels, engine, note = _classify_all(texts, hf_token, model.strip() or DEFAULT_MODEL)
    for text, (label, score) in labels.items():
        for item in seen[text]:
            item.sentiment_label = label
            item.sentiment_score = score

    for topic in topics:
        topic.sentiment = _profile(topic.evidence, engine)

    if len(seen) > MAX_TEXTS:
        skipped = f"Tone was measured on the {MAX_TEXTS} most distinct headlines out of {len(seen)}."
        note = f"{note} {skipped}".strip()
    return note
