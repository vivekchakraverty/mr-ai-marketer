---
title: Mail Tracker
emoji: 📬
colorFrom: gray
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

Minimal open/click tracking + event-sync endpoint for Mr AI Marketer's email
tracking feature. Public by necessity (recipients' mail clients hit it directly
from wherever they are), but not meant to be browsed — there's no UI here.

Endpoints: `GET /o/{token}.gif` (open pixel), `GET /c/{token}?u=<url>` (click
redirect), `GET /events?since_id=&secret=` (secret-gated sync feed for the app's
own local backend).
