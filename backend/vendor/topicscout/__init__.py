"""Topic Scout — evidence-led topic discovery with sentiment.

Two upstream projects merged into one pipeline:

* **TrendScout** (the user's own Streamlit app, V:/trend_scout_app) contributes the
  discovery and ranking model: editorial/institutional feeds nominate candidate
  phrases, then each candidate is measured current-window-vs-preceding-equal-window
  across five evidence families and scored with separate momentum and confidence.
* **TrendScope** (github.com/mamboyepez17/trendscope, MIT) contributes the social and
  consumer signal its source stack was missing — Reddit, Google Trends, YouTube,
  TikTok, Amazon, Twitter/X — plus the idea of a sentiment layer over the evidence.

The merge is deliberately one-directional: TrendScope's sources are re-expressed as
TrendScout ``Evidence`` so they flow through the same relevance filter, syndication
dedupe, per-source caps, and family weighting as everything else. A social source
does not get to bypass the ranking model just because it is noisier.

Sentiment does NOT feed the momentum score. Momentum is a measurement of change;
sentiment is a description of tone. Mixing them would make a score that rises for
both "this is growing" and "people are happy about it", which is not interpretable.
They stay as two separate axes on the same topic.
"""
