# ac_social_ads — Social & Ads Platform Integration for Odoo 19

**Status**: Plan finalized (research complete) — ready for review before scaffolding
**Date**: 2026-08-09

## Goal
Build `ac_social_ads` in `/opt/odoo19/projects/AlshayebCo/` — a single Odoo 19 module integrating
**Meta (Facebook/Instagram)** and **Google Ads** first; X (Twitter), TikTok, LinkedIn later via the
same internal platform abstraction.

**Confirmed scope:**
- Full **two-way management**: pull structure + metrics + leads; push create/update/pause/budget from Odoo
- Leads become **`crm.lead`** records with attribution (platform, campaign, ad set, ad, gclid)
- **Single module** `ac_social_ads`, platform-agnostic core models with per-platform service layers
- No credentials exist yet → developer-account setup is part of the plan (Phase 0)

**Research reports** (full detail with citations): `files/meta-api-research.md`, `files/google-ads-research.md`
**Skill**: `meta-api-expert` already installed at `/opt/odoo19/.claude/skills/meta-api-expert/` (verified identical to the zip).

---

## Key research findings that shape the design

### Meta Marketing API (current: v26.0, ~quarterly releases, 2-yr support)
- **Auth**: System User token (non-expiring) for server sync; **long-lived Page token** required for lead retrieval (rate-limited per Page, not per user). Enable App Secret Proof.
- **Permissions**: `ads_read`, `ads_management`, `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `pages_manage_ads`. `leads_retrieval` + `pages_manage_ads` need **App Review + Business Verification**; managing other people's accounts needs Advanced Access to ads_read/ads_management. Access tiers: Limited → Full (needs ≥500 calls/15d, <15% errors).
- **Hierarchy**: Ad Account (`act_<id>`) → Campaign → Ad Set → Ad → Creative (creatives are immutable). Lead forms belong to the **Page**, not the ad account.
- **Insights**: `/insights` edge at every level; `date_preset`/`time_range`, `time_increment=1` for daily rows, `level=`, `breakdowns`, sync or async (`POST` → poll `report_run_id` → GET results). Actions come as arrays (`action_type`/`value`) — map `lead`, `purchase`, etc. BUC rate limits: Limited `600+400×active_ads`/h; monitor `X-FB-Ads-Insights-Throttle` + `X-Business-Use-Case-Usage`, backoff on error 4/613, subcode 1487534 = reduce range.
- **Leads**: real-time via **Webhooks** (app subscribes to Page `leadgen` field → POST with `leadgen_id` → `GET /{leadgen_id}` with Page token → `field_data` array of `{name, values[]}`). Plus bulk/incremental fallback: `GET /{form_id}/leads?filtering=[time_created GREATER_THAN ts]`. Webhook handshake: GET echo `hub.challenge`; POST signed with `X-Hub-Signature-256` (app secret).
- **SDK**: `facebook_business` (PyPI), actively maintained; pin `api_version='v26.0'`.
- **Meta devtools MCP** = AI-agent dev tooling only; NOT a production integration path.

### Google Ads API (v25)
- **Auth = 4 credentials together**: developer token (MCC → API Center), OAuth2 client id/secret (GCP), refresh token (one-time OAuth flow) OR service-account JSON (must impersonate a user with account access), and `login_customer_id` (MCC id).
- **Access tiers**: Test (auto) → Explorer (2,880 ops/day prod) → **Basic (15,000/day — apply immediately, ~5 days)** → Standard (unlimited, ~10 days).
- **Hierarchy**: Customer (MCC → client accounts) → Campaign → AdGroup (= "ad set") → AdGroupAd → Ad.
- **GAQL** (SQL-like, `FROM` required) via `GoogleAdsService.search_stream` (1 operation regardless of size). Costs in **micros** (÷1,000,000). Date filters: `segments.date DURING LAST_30_DAYS` or `BETWEEN`.
- **Leads**: `lead_form_submission_data` resource (poll-only, **30-day retention** — sync must run at least daily, ideally hourly). Fields: id, campaign, ad_group, ad_group_ad, asset, **gclid**, submission_date_time, nested `lead_form_submission_fields[]` (field_type enum FULL_NAME/EMAIL/PHONE_NUMBER/... + field_value). **No API webhook**, but Google Ads UI lead-form webhook (separate from API) can POST near-real-time to our endpoint → use as fast path + polling as dedup fallback.
- **Push back**: `ConversionUploadService.UploadClickConversions` with gclid (+ SHA-256 hashed PII for Enhanced Conversions for Leads) — upload won deals from CRM. Mutate = max 10,000 ops/request.
- **SDK**: `google-ads` (PyPI, Python 3.9+, gRPC). Init via `GoogleAdsClient.load_from_dict()` from Odoo-stored config.
- **google-ads-mcp** = AI-agent dev tooling only (same library underneath); useful for prototyping GAQL, NOT the production path.

---

## Module architecture: `ac_social_ads`

### Models (platform-agnostic core + per-platform payloads)
| Model | Purpose |
|---|---|
| `ads.account` | Connected account: `platform` (meta/google/x/tiktok/linkedin), name, ext ids, credentials (token, refresh token, developer token, app secret, page tokens), state, last-sync timestamps, "Test Connection" button |
| `ads.campaign` | Unified campaign: platform, ext_id, name, status, effective_status, objective/channel, budgets, start/stop, `account_id`, raw JSON |
| `ads.adset` | Meta Ad Set / Google Ad Group: campaign_id, targeting summary, optimization_goal, budgets, status |
| `ads.ad` | Ad: adset_id, status, creative summary (title/body/image/video), raw JSON |
| `ads.insight` | Daily metric rows: date, level (account/campaign/adset/ad), object ref, impressions, reach, clicks, spend, ctr, cpc, cpm, conversions, leads, actions JSON. Unique per (object, date) |
| `ads.lead` | Staging: platform, ext lead id (dedup, unique), form ref, field_data JSON, campaign/adset/ad links, gclid, state (new/mapped/failed), `crm_lead_id` |
| `ads.lead.form` | Lead forms (Meta Page forms, Google lead assets) + per-form field mapping to crm.lead fields |
| `ads.sync.log` | Audit per run: account, job type, status, counts, error detail, duration |
| `ads.job.queue` | Outbound writes (create/update/pause/budget, conversion upload) with retry + failure reason |

### Service layer
- `ads.platform.service` abstract: `sync_structure()`, `sync_insights()`, `sync_leads()`, `push_changes()`, `test_connection()`
- `meta.service` (facebook_business) + `google.service` (google-ads) implementations; future x/tiktok/linkedin plug in here

### Sync engine (ir.cron)
| Job | Frequency | Notes |
|---|---|---|
| Structure sync (campaigns→adsets→ads) | Every 6h per account | Meta edges; Google GAQL on campaign/ad_group/ad_group_ad |
| Insights sync | Nightly (+ every few hours for today) | Meta: `time_increment=1`, `level=` per object, async jobs for big ranges; Google: `segments.date` + `search_stream` |
| Lead poll (Google + Meta fallback) | Hourly | Google: 30-day retention makes this critical; Meta: `time_created GREATER_THAN last_ts` |
| Outbound queue runner | Every 5–15 min | Retry with exponential backoff (Meta err 4/613; Google RESOURCE_TEMPORARILY_EXHAUSTED) |
| Token health check | Daily | Meta page-token expiry alerts; Google refresh-token validity |

### Leads → CRM
- **Meta webhook controller** `/ac_social_ads/webhook/meta`: GET handshake (`hub.challenge` echo, verify token per account), POST with strict `X-Hub-Signature-256` verification (same pattern as `ac_whatsapp_connector` — no fallback routing, matched account only, `sudo()` scoped)
- **Google lead webhook controller** `/ac_social_ads/webhook/google`: shared-secret key check (Google lead-form webhook sends `google_key`); polling dedups
- Webhook payload → `ads.lead` staging → **async** mapping to `crm.lead` (name/email/phone from field_data per form mapping; `source_id`/`medium_id`/campaign tracking; description = full field dump; return HTTP 200 fast)
- Dedup: unique constraint on (platform, ext_lead_id)
- Handle Meta `data_deletion_request` endpoint for GDPR

### Two-way management (Phase 3)
- Campaign/adset/ad form buttons: Activate / Pause / Archive, Edit budget (wizard with guardrails: Meta max 4 budget changes/hour per ad set)
- Changes → `ads.job.queue` → platform service pushes → status reconciled on next structure sync
- Campaign creation wizard (Meta: objective + special_ad_categories mandatory; Google: channel type + bidding strategy) — initially simple lead-gen/traffic templates
- Conversion upload: on `crm.lead` won → upload click conversion to Google (gclid); Meta Conversions API optional later

### Security
- Groups: `group_ads_user` (read) / `group_ads_manager` (manage accounts, push changes)
- `security/ir.model.access.csv` (mandatory per repo convention)
- Credential fields: manager-group-only (`groups=` attribute), never in views for users; consider `password=True` widgets

### Views/Menus
- Top menu "Ads" (category `AlshayebCo/Marketing`): Accounts, Campaigns, Ad Sets, Ads, Insights (list/pivot/graph), Leads, Lead Forms, Sync Logs, Job Queue, Configuration
- Dashboard: pivot/graph over `ads.insight`; later ROAS vs sale.order (Phase 5)

### Dependencies
```python
'depends': ['crm', 'mail', 'utm'],
'external_dependencies': {'python': ['facebook_business', 'google.ads']},  # pip: facebook_business, google-ads
```

---

## Phase 0 — Credentials & app setup (docs in module README/DEVELOPMENT.md)

**Meta:**
1. developers.facebook.com → create Business app → add Marketing API + Webhooks products
2. Business Settings → System Users → create system user, assign ad accounts + Pages, generate token with scopes above
3. Get long-lived Page token per Page (user token → 60-day exchange → never-expiring page token)
4. Configure Webhooks: callback `https://<odoo-host>/ac_social_ads/webhook/meta`, verify token, subscribe Page `leadgen` field; `POST /{page-id}/subscribed_apps?subscribed_fields=leadgen`
5. App Review for `leads_retrieval`, `pages_manage_ads`, Advanced Access `ads_read`/`ads_management` + Business Verification (needed for production leads)
6. Enable App Secret Proof

**Google:**
1. MCC manager account → API Center → developer token (works on test accounts immediately)
2. Apply for **Basic Access** immediately (15,000 ops/day; Explorer's 2,880 is too tight)
3. GCP project → enable Google Ads API → OAuth consent screen + client ID/secret
4. One-time OAuth flow (scope `https://www.googleapis.com/auth/adwords`) → refresh token
5. Configure lead-form webhook in Google Ads UI (fast path) pointing at Odoo endpoint
6. `pip install google-ads facebook_business` into `/opt/odoo19/odoo-venv`

## Phases (detailed — mirrors the todo tracker)

### Phase 0 — Credentials & Scaffold
| # | Task | Detail |
|---|---|---|
| P0.1 | Meta app & tokens | Business app + Marketing API & Webhooks products; System User token (scopes: ads_read, ads_management, leads_retrieval, pages_manage_metadata, pages_show_list, pages_read_engagement, pages_manage_ads); long-lived Page tokens; App Secret Proof; App Review (leads_retrieval, pages_manage_ads) + Advanced Access (ads_read/ads_management) + Business Verification |
| P0.2 | Google credentials | MCC developer token (API Center); apply for Basic Access (15k ops/day) immediately; GCP project + enable API + OAuth consent + client ID/secret; one-time OAuth flow (adwords scope) → refresh token; record login_customer_id |
| P0.3 | Scaffold `ac_social_ads` | Standard structure; manifest (depends: crm, mail, utm; ext deps: facebook_business, google-ads); DEVELOPMENT.md; README with setup docs |
| P0.4 | venv deps | `pip install facebook_business google-ads` into /opt/odoo19/odoo-venv; verify on Python 3.12 |

### Phase 1 — Core models + read sync (Meta & Google)
| # | Task | Detail |
|---|---|---|
| P1.1 | Core models | ads.account (creds manager-only, Test Connection), ads.campaign/adset/ad (ext_id, status, budgets, raw JSON), ads.insight (daily rows, unique per object+date), ads.sync.log, ads.job.queue; groups + access CSV + menus |
| P1.2 | Meta service | facebook_business pinned v26.0; structure sync (campaigns→adsets→ads); insights time_increment=1 per level; action-array mapping; backoff on err 4/613; throttle headers; async reports |
| P1.3 | Google service | load_from_dict from account; GAQL search_stream on campaign/ad_group/ad_group_ad; segments.date daily; micros→currency; backoff on RESOURCE_TEMPORARILY_EXHAUSTED |
| P1.4 | Sync engine + crons | Structure every 6h; insights nightly + intraday; sync.log audit; Sync Now buttons; concurrency guard; failure notifications |
| P1.5 | Views | List/form/search for all objects; insights pivot/graph; smart buttons campaign→adsets/ads/insights/leads |
| P1.6 | Phase 1 tests | Mocked clients: constraints, upsert idempotency, parsers, backoff |

### Phase 2 — Leads → CRM
| # | Task | Detail |
|---|---|---|
| P2.1 | Lead models | ads.lead (ext_lead_id unique, field_data JSON, gclid, state, crm_lead_id); ads.lead.form with per-field mapping to crm.lead |
| P2.2 | Meta webhook | GET handshake (hub.challenge); POST HMAC X-Hub-Signature-256 (whatsapp-connector pattern); async fetch /{leadgen_id}; fallback bulk poll /{form_id}/leads incremental; GDPR data-deletion endpoint |
| P2.3 | Google leads | Hourly GAQL poll lead_form_submission_data (30-day retention → alerting on failure); webhook controller with google_key for UI-configured fast path; dedup |
| P2.4 | CRM mapping | ads.lead → crm.lead with utm source/medium/campaign, field dump in description, assignment rules, activities, failure states |
| P2.5 | Phase 2 tests | Signature validation, payload parsing, dedup, crm.lead creation, retry |

### Phase 3 — Two-way management
| # | Task | Detail |
|---|---|---|
| P3.1 | Outbound queue | Job processing with backoff, max attempts, guardrails (Meta 4 budget-changes/h/adset; Google 10k ops/mutate) |
| P3.2 | Status & budget actions | Activate/Pause/Archive buttons; Edit Budget wizard; pending_sync state; platform-wins conflict policy |
| P3.3 | Creation wizards | Meta (objective, special_ad_categories, budget, targeting, creative) + Google (channel type, bidding, budget) |
| P3.4 | Conversion upload | crm.lead won → UploadClickConversions with gclid; Enhanced Conversions (SHA-256 normalized PII); discover UPLOAD_CLICKS actions via GAQL; Meta CAPI optional |
| P3.5 | Phase 3 tests | Queue, reconciliation, wizard validation, hashing/normalization |

### Phase 4 — More platforms (after Meta+Google stable)
| # | Task | Detail |
|---|---|---|
| P4.1 | X (Twitter) | OAuth2; campaigns/line items/promoted tweets sync; analytics; lead cards |
| P4.2 | TikTok | TikTok Marketing API; advertiser OAuth; structure + reporting + lead gen |
| P4.3 | LinkedIn | Marketing API; rw_ads/r_ads scopes; campaigns/creatives/analytics; lead gen form responses |

### Phase 5 — Dashboards & go-live
| # | Task | Detail |
|---|---|---|
| P5.1 | Dashboards & ROAS | Pivot/graph over insights (CPL, CPC, spend trends); ROAS joining won crm.lead revenue to spend; per-account comparison |
| P5.2 | Hardening | Token encryption-at-rest, monitoring alerts, docs, migrations, indexes, final App Review/access verification |

## Risks / open items
- Meta App Review + Business Verification lead time (start early; blocks production leads)
- Google Basic Access approval (~5 business days)
- Webhooks need public HTTPS on the Odoo host (dev: tunnel)
- Google lead data 30-day retention → cron reliability + alerting on failed syncs
- Meta API version churn (~quarterly) → pin version, centralize in one constant
- Token storage: manager-only fields now; evaluate encryption-at-rest before go-live
