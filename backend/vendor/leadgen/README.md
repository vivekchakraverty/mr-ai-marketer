# Lead Gen Agent

A local, free/open-source AI sales agent embedded in Mr. AI Marketer — an OpenOutreach-style
pipeline (discover → qualify → find & verify email → draft → send → agentic follow-up) that
runs on your own machine and uses no paid third-party APIs.

You **operate** it in **Research / Strategy → Lead Gen Agent** and **observe** its CRM in
**Analytics → Outreach CRM**.

## How it works

1. **Discover** — an LLM turns your product description into search criteria, and two free
   backends find matching businesses: **OpenStreetMap/Overpass** (structured, keyless) and a
   **self-hosted SearXNG** metasearch. Each company's public site is lightly enriched.
2. **Qualify** — a per-campaign **Gaussian Process** with **Bayesian active learning**
   (scikit-learn, over local `bge-small-en-v1.5` embeddings) learns your taste from a handful
   of LLM judgments and generalizes to the rest, so most leads are scored without an LLM call.
3. **Find email** — a deterministic pattern finder proposes addresses, verified by
   self-hosted **Reacher**. Nothing is sent to an unverified address. Gated by your daily
   send capacity.
4. **Draft & send** — openers are written by your fine-tuned **Email Writer** model (with a
   CTR estimate) and, by default, wait in a **review queue** for your approval before sending
   from your own mailbox over SMTP. Flip **auto-send** on for full autonomy.
5. **Follow up** — replies are read over IMAP; a single structured LLM call decides whether to
   reply (drafted by the Email Writer), wait, or close the deal.

Only the reasoning calls bill to your Hugging Face token; discovery, verification, embeddings,
and email drafting are free/self-hosted.

## Quickstart (inside the app)

1. **Open** Research / Strategy → **Lead Gen Agent**.
2. **Set up the lead engine** — the first time you Start a campaign (or press Run now), a
   one-click setup brings up the self-hosted **Reacher** + **SearXNG** services using the same
   local Docker/WSL2 layer the Distribution engine already uses. No Docker Desktop needed.
3. In **Settings**, add your **Hugging Face token** (shared with the other tools) and your
   **mailbox** (SMTP + IMAP). Use the per-field **Test** buttons.
4. **Create a campaign**: describe what you sell, who you sell to, and your objective. Leave
   **auto-send off** to review every email first (recommended).
5. **Start** the campaign (or **Run now** for an immediate step). Watch the pipeline fill in
   under **Analytics → Outreach CRM**, and approve drafts from the **review queue**.

## Standalone CLI (diagnostics)

From `backend/` with the venv active:

```bash
python -m vendor.leadgen.config --verify all         # test HF/LLM/SMTP/IMAP/Reacher/SearXNG
python -m vendor.leadgen.cli status                  # config + campaign summary
python -m vendor.leadgen.cli add-campaign --name "Austin clinics" \
    --product "Booking widget for dental clinics" --country US --activate
python -m vendor.leadgen.cli run-once --campaign <id>   # drive one campaign to idle
python -m vendor.leadgen.cli export --campaign <id> --out deals.json
```

(The `email` step needs the app's Email Writer, injected only when running inside the backend;
from the bare CLI, discovery/qualification/email-finding run but opener drafting is skipped.)

## Data & privacy

See [PRIVACY.md](PRIVACY.md). Everything is stored locally in SQLite under `DATA_DIR/leadgen/`;
only public business data is collected; the suppression list is honored on every send and
never purged.
