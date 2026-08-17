# The Intrinsic Value Podcast Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add The Intrinsic Value Podcast to the VPS production pipeline and import its latest ten eligible videos into the WeChat draft box without changing application code or cron.

**Architecture:** Update the two JSON configuration objects already stored in Supabase, seed one idempotent source entry in the existing processing state, and invoke the current batch command once with a ten-video channel window. The existing daily cron then handles future videos incrementally.

**Tech Stack:** Python 3.12, PostgreSQL/Supabase JSONB, yt-dlp, Gemini writer, WeChat Official Account draft API

---

### Task 1: Back up production configuration and state

**Files:**
- Create on VPS: `/root/overlord/backups/intrinsic-value-<UTC timestamp>.json`

- [ ] **Step 1: Read `sources`, `writer_profiles`, and `state` in one read-only transaction.**
- [ ] **Step 2: Write them to a timestamped JSON file under `/root/overlord/backups/`.**
- [ ] **Step 3: Parse the backup and verify it contains all three keys.**

### Task 2: Add the source and conditional writer profile

**Data modified:**
- Supabase `overlord_config` row `sources`
- Supabase `overlord_config` row `writer_profiles`
- Supabase `overlord_state` row `state`

- [ ] **Step 1: In one transaction, upsert source name `The Intrinsic Value Podcast` with priority 5, minimum duration 600, profile `intrinsic-value-auto`, and destination `wechat_draft`.**
- [ ] **Step 2: Upsert the conditional profile under the key `intrinsic-value-auto`.**
- [ ] **Step 3: Seed `state.sources["the-intrinsic-value-podcast"]` only when it is absent.**
- [ ] **Step 4: Commit the transaction.**

### Task 3: Verify effective production configuration

**Commands:**
- `load_source_config(Path("config/sources.json"))`
- `load_writer_profile("intrinsic-value-auto")`

- [ ] **Step 1: Verify exactly one effective source has the configured name and URL.**
- [ ] **Step 2: Verify its priority, duration, destination, and writer profile.**
- [ ] **Step 3: Verify the conditional writer profile loads from Supabase and contains both routing branches.**
- [ ] **Step 4: Confirm the crontab is unchanged.**

### Task 4: Import and publish the latest ten videos

**Command:**
- `PYTHONPATH=src .venv/bin/python scripts/process_sources.py --generate-article --push --max-items 10 --gemini-model gemini-flash-latest,gemini-2.5-pro,gemini-2.5-flash,gemini-3.5-flash --channel-limit 10`

- [ ] **Step 1: Run a source-scoped dry-run check and confirm the ten candidates belong to The Intrinsic Value Podcast.**
- [ ] **Step 2: Run the production import command from `/root/overlord`.**
- [ ] **Step 3: Monitor the run through completion without changing cron.**
- [ ] **Step 4: Count generated articles, successful WeChat draft media IDs, and failures.**
- [ ] **Step 5: Verify the processing state records the ten selected video IDs.**

### Task 5: Final production audit

- [ ] **Step 1: Re-read effective source and profile configuration.**
- [ ] **Step 2: Confirm cron still runs daily at UTC 13:25 with its original command.**
- [ ] **Step 3: Report the backup path, imported video titles, applied routing outcomes, draft results, and any retryable failures.**
