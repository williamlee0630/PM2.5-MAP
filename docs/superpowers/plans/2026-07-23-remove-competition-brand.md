# Remove Competition Brand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all public associations with「智在家鄉」and the MediaTek competition while preserving the site's existing GEO/SEO structure and functionality.

**Architecture:** Perform a narrowly scoped content migration across public HTML, machine-readable discovery files, documentation, and the deployable n8n workflow. Preserve URLs and schema types, remove only the competition entity and wording, then validate absence, JSON-LD, XML, and the final diff.

**Tech Stack:** Static HTML, JSON-LD, XML sitemap, plain-text `llms.txt`, n8n JSON workflow, Git.

## Global Constraints

- The canonical site identity remains「空污共犯 PM2.5 智慧監測平台」.
- Do not change URLs, canonical links, schema types, layout, JavaScript behavior, routes, or data sources.
- Remove every occurrence of「智在家鄉」and every explicit MediaTek competition/submission association from public and deployable content; non-deployed task documentation and Git history may retain the terms for traceability.
- Set affected homepage `dateModified` and sitemap `lastmod` to `2026-07-23`.

---

### Task 1: Public page and machine-readable identity cleanup

**Files:**
- Modify: `web/index.html`
- Modify: `web/routing.html`
- Modify: `web/llms.txt`
- Modify: `README.md`

**Interfaces:**
- Consumes: Existing public HTML metadata and schema graph.
- Produces: Competition-neutral public copy while retaining the existing site identity and schema IDs.

- [x] **Step 1: Record the failing content assertion**

Run: `rg -n "智在家鄉|聯發科技|數位社會創新競賽|為.*競賽.*打造" README.md web/index.html web/routing.html web/llms.txt`

Expected: Matches are reported in each affected public identity surface.

- [x] **Step 2: Replace competition-oriented copy**

Update the homepage title to `空污共犯：讓每一次移動科技成為守護城市呼吸的力量 | PM2.5 智慧監測平台`; describe the platform directly as a vehicle-mounted PM2.5 observation and route-research platform; remove the competition `Event` object from `mentions`; replace competition keywords with `台北空氣品質`, `移動空污感測`, and `健康路線`; and rewrite the FAQ answer as a direct project definition. Apply the same neutral identity to `routing.html`, `llms.txt`, and `README.md`.

- [x] **Step 3: Verify public identity cleanup**

Run: `rg -n "智在家鄉|聯發科技|數位社會創新競賽|為.*競賽.*打造" README.md web/index.html web/routing.html web/llms.txt`

Expected: Exit code 1 with no output.

### Task 2: Deployable workflow cleanup and freshness update

**Files:**
- Rename: `web/智在家鄉_PM25_六級分級_n8n匯入.json` to `web/空污共犯_PM25_六級分級_n8n匯入.json`
- Modify: `web/空污共犯_PM25_六級分級_n8n匯入.json`
- Modify: `web/sitemap.xml`
- Modify: `web/index.html`

**Interfaces:**
- Consumes: Existing n8n workflow semantics and current sitemap URLs.
- Produces: A brand-neutral workflow artifact and refreshed modification dates.

- [x] **Step 1: Rename the deployable workflow file**

Use Git-aware rename so file history is retained. Do not change workflow node IDs, credentials, webhook settings, or JavaScript behavior.

- [x] **Step 2: Replace workflow display strings and update dates**

Replace the workflow name, cached result label, embedded document title, and info-box title with「空污共犯」variants. Change affected homepage `dateModified` and root sitemap `lastmod` from `2026-07-14` to `2026-07-23`.

- [x] **Step 3: Verify zero repository residue**

Run: `rg -n --hidden -g '!.git' -g '!docs/**' "智在家鄉|聯發科技|數位社會創新競賽|為.*競賽.*打造|參與.*競賽|投稿" README.md web server compress_archive.py`

Expected: Exit code 1 with no output.

### Task 3: Structured-data and regression verification

**Files:**
- Test: `web/*.html`
- Test: `web/*.json`
- Test: `web/sitemap.xml`

**Interfaces:**
- Consumes: Updated static site files.
- Produces: Evidence that machine-readable content remains parseable and the change stayed in scope.

- [x] **Step 1: Parse every JSON-LD block**

Run a read-only Node.js script that extracts each `<script type="application/ld+json">` block from every `web/*.html` file and calls `JSON.parse` on it.

Expected: Every block parses; the command exits 0.

- [x] **Step 2: Parse workflow JSON and sitemap XML**

Run: `node -e "JSON.parse(require('fs').readFileSync('web/空污共犯_PM25_六級分級_n8n匯入.json','utf8')); console.log('workflow JSON OK')"`

Expected: `workflow JSON OK`.

Parse `web/sitemap.xml` with an available XML parser or perform a strict structural check when no XML package is installed.

- [x] **Step 3: Review the final diff**

Run: `git diff --check` and `git diff -- README.md web/index.html web/routing.html web/llms.txt web/sitemap.xml "web/*PM25*json"`

Expected: No whitespace errors; changes are limited to brand copy, the competition entity removal, modification dates, and the workflow filename/display strings.
