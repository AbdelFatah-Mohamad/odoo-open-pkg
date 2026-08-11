# AC Ads Connector — Development Log

## Overview
Provider-pluggable integration between Odoo 19 and ad platforms. V1: Meta Marketing API v26 +
Google Ads API v25 via their official SDKs. Pulls campaigns / ad sets / ads / creatives / daily
insights / lead-form leads into Odoo, auto-creates `crm.lead` with UTM attribution + dedup, and
(later phases) pushes status/budget/create edits, audiences, and Google click conversions back.
Standalone: depends only on `crm`, `utm`, `mail` — no enterprise dependency.
Full plan: `/home/obada/.claude/plans/we-have-a-new-nifty-manatee.md` (phases P0–P10).
Superseded early draft: `docs/kimi-plan-reference.md`.

## Current Status
**Status**: V2 (Integration Wave, restructured per user decision to TWO modules).
Core 19.0.2.0.0 = everything Community-safe: full platform sync, leads→CRM,
write-back, audiences, conversion upload, click-ID capture (gclid/fbclid — NO
Odoo module does this natively), URL auto-tagging, link-tracker short links,
ROAS/CPL report AND the built-in spreadsheet dashboard (folded in from the former
_spreadsheet satellite). **ac_ads_connector_extend** = the single enterprise
companion (manual install, pulls sale_crm + social_facebook + marketing_automation):
sales ROAS, Social bridge, MA instant intake + Welcome Ad Leads template.
Former satellites _social/_sale/_marketing_automation/_spreadsheet are DELETED —
their code lives in extend (or base for the dashboard).
**Last Updated**: 2026-08-10

## Integration Wave gotchas
- NEVER extend `utm.mixin.tracking_fields()` with fields that don't exist on every
  utm model: `link_tracker.create()` force-sets every listed field → ValueError.
  Click-id capture therefore lives in our own `ir.http._post_dispatch` hook
  (models/ir_http.py) + a crm.lead default_get override.
- SQL views can't be extended piecemeal: `ac_ads_connector_sale` restates the FULL
  performance-report SQL in its init() — keep in sync with the core report file.
- MA participant sync is a 12-hour cron; `_trigger()` on
  `marketing_automation.ir_cron_campaign_sync_participants` is the supported
  immediacy hook. MA campaign templates are registered in PYTHON
  (get_campaign_templates_info), never data XML.
- Dashboard `.osheet.json`: chart-only payloads are hand-authorable (figures with
  metaData/searchParams); grid/pivot dashboards need the UI editor export.

## Stopping Point (final for V1)
V1 done end-to-end. 15 models + 4 wizards + 1 SQL-view report, 16 test files worth of
coverage (120 tests), 8 crons, 6 controller routes. To go live: follow README credentials
walkthrough (Meta system-user token + App Review for leads; Google developer token +
Basic Access), connect an account, Test Connection, Sync Structure. NOT committed — the
user lands the work.

## Previous Stopping Point
Phases 0–2 COMPLETE and verified (43/43 tests green on `ads_test`). Structure pull live:
ads.sync.mixin (state machine fields, dirty-tracking dormant), ads.campaign
(_inherits utm.campaign — display name = delegated `title`, `_rec_name='title'`; unlink
archives utm when leads reference it), ads.adset/ads.ad/ads.creative, both providers'
list_* generators + normalization tables, ads.sync.engine (hourly cron, idempotent
upsert, per-record + per-account isolation, local-pending clobber guard, entity-level
cursor), Sync Structure button, campaign kanban/list/form + adset/ad views + menus.
Deliberate deviations: Meta updated_time delta filtering deferred (full pull is
idempotent; filter-field syntax has silent-miss risk) — revisit in Phase 5; Google raw
payload = scalar summary only (no proto→dict conversion). Next: P3.1 ads.insight.

## [TECH_STACK]
- Odoo 19 Community-compatible (crm, utm, mail)
- Python 3.12 venv `/opt/odoo19/odoo-venv`
- facebook-business >=26,<27 (Meta Marketing API v26, version pinned per-account field)
- google-ads >=31,<32 (Google Ads API v25, REST/gRPC via SDK; google-auth for SA credentials)

## [SYSTEM_FLOW]
1. Admin creates `ads.account` (provider meta/google), pastes credentials, Test Connection → state=connected (currency/tz captured).
2. Crons: structure (hourly) → insights (daily + intraday + 28d restatement window + backfill) → leads (15 min, watermark−1d overlap) → push queue (5 min) → token health (daily) → insight compaction (monthly).
3. Leads: platform → `ads.lead` staging (idempotent on external_id) → dedup (email/phone) → `crm.lead` with utm campaign/medium/source.
4. Write-back: dirty-tracking on pushable fields → `ads.push.job` queue → SDK mutate → reconcile; conflicts staged in `remote_snapshot` with resolve actions.
5. Won gclid leads → Google click-conversion upload (Phase 9).

## [ARCHITECTURE]
- `__manifest__.py` — app, category AlshayebCo/Marketing, external deps facebook_business + google.ads
- `tools/ads_api.py` — AdsApiError(failure_type…), AdsApiBase (`_execute` choke point: cooldown pre-flight, redacted logging, retry/backoff, `_map_exception`/`_capture_rate_state` hooks), normalizers (minor/micros/dt/hash_pii), PROVIDER_API dict [PENDING]
- `tools/meta_api.py` — MetaAdsApi over facebook_business, per-account FacebookSession (no global init), BUC header parsing [PENDING]
- `tools/google_api.py` — GoogleAdsApi over google-ads (load_from_dict / SA credentials, no impersonation), GAQL builders + `_gaql_quote` [PENDING]
- `models/ads_account.py` — credentials (admin-group fields), state, cooldown, watermarks, `_get_api()`, `action_test_connection`, `_cron_token_health` [PENDING]
- `models/ads_sync_log.py` — run log + `_job_start/_job_log/_job_finish` + GC [PENDING]
- `wizard/google_auth_wizard.py` + `controllers/main.py` — Google OAuth refresh-token flow [PENDING]
- `security/ads_security.xml` + `ir.model.access.csv` + `data/neutralize.sql` [PENDING]
- `views/ads_account_views.xml`, `views/ads_sync_log_views.xml`, `views/ads_menus.xml` [PENDING]
- Later phases add: ads_sync_mixin/ads_sync_engine/ads_campaign/ads_adset/ads_ad/ads_creative/
  ads_insight/ads_lead_form/ads_lead_field_map/ads_lead/ads_audience/ads_push_job/crm_lead,
  webhooks, conversion upload, report SQL view (see plan P2–P10)

## [ORPHANS & PENDING]
- [x] P0.1 annotate meta-api-expert skill (warning headers, 4 files)
- [x] P0.2 absorb ac_social_ads → docs/kimi-plan-reference.md, folder removed
- [x] P0.3 pip install SDKs + smoke imports (fb 26.0.0, google-ads 31.2.0)
- [x] P0.4 module skeleton + manifest + requirements.txt
- [x] P0.5 DEVELOPMENT.md + README.md bootstrap
- [x] P1.1 tools/ads_api.py (AdsApiError, AdsApiBase, normalizers, PROVIDER_API)
- [x] P1.2 tools/meta_api.py (MetaAdsApi)
- [x] P1.3 tools/google_api.py (GoogleAdsApi + client builder)
- [x] P1.4 models/ads_account.py
- [x] P1.5 models/ads_sync_log.py
- [x] P1.6 google OAuth wizard + /ads/google/oauth_callback controller
- [x] P1.7 security xml + access csv + neutralize.sql + utm_data.xml
- [x] P1.8 account + sync log views + Ads root menu
- [x] P1.9 tests (common fakes, meta, google, security) + first `-i` install green (31/31)
- [x] P2.1 models/ads_sync_mixin.py (fields + dormant dirty-tracking)
- [x] P2.2 mirror models: ads_campaign (_inherits utm.campaign, title), ads_adset, ads_ad, ads_creative
- [x] P2.3 SDK read surface: Meta list_campaigns/adsets/ads + Google GAQL _q_* + normalization tables
- [x] P2.4 models/ads_sync_engine.py (_cron_sync_structure, _upsert_page, cursors)
- [x] P2.5 hourly structure cron + action_sync_structure (Sync Now)
- [x] P2.6 campaign/adset/ad views + menus
- [x] P2.7 tests (43/43: idempotency, parent linking, both providers' mapping, isolation, utm dedup/unlink)
- [x] P3.1 models/ads_insight.py (+UniqueIndex, _upsert_from_rows, month compaction w/ merge)
- [x] P3.2 Meta iter_insights + async job trio + too_much_data recursive splitting
- [x] P3.3 Google iter_insights (GAQL segments.date per level resource)
- [x] P3.4 engine: _cron_sync_insights (28d rolling window, backfill chaining w/ cron
      re-trigger, async job polling) + 3 crons (daily/4h-today/monthly-compact)
- [x] P3.5 KPI rollups (spend/impressions/clicks/leads/cpl 30d) + Dashboard + Reporting menus
- [x] P3.6 tests — suite now 56/56 green (incl. deliberate-crash isolation tests)
- [x] P4.1 ads_lead_form + ads_lead_field_map (+DEFAULT_QUESTION_MAP auto-mapping, idempotent)
- [x] P4.2 ads_lead staging (idempotent upsert, extract contact, dedup email/phonenumbers-E164,
      3 duplicate policies, description Q&A dump, country/state m2o resolution, attribution
      re-resolution via platform_refs)
- [x] P4.3 Meta Page/LeadgenForm/Lead surface (Page-token preference, LEADS_PER_FORM=True) +
      Google lead_form_submission_data GAQL (LEADS_PER_FORM=False, engine routes per form)
- [x] P4.4 engine _cron_sync_leads (15-min cron, watermark=max(created_time)−1d overlap,
      _process_pending tail w/ re-trigger) + 24h staleness alert in token-health cron
- [x] P4.5 crm.lead smart button + process wizard (force reprocess) + Leads menu + ACL/rules
- [x] P4.6 tests — suite now 70/70 green
- [x] P5.1 _ADS_PUSHABLE_FIELDS + pending_changes tracking (exact changed-field sets) +
      conflict detection (_remote_differs/_stage_remote, chatter+activity) + resolve actions
- [x] P5.2 ads_push_job (update/set_status/set_budget, retry taxonomy, commit-per-job,
      cooldown deferral, MAX_ATTEMPTS=5 backoff, button_retry)
- [x] P5.3 Meta api_update surface + Google mutate services w/ update_mask (ad content
      immutable on Google — only status pushes for ads)
- [x] P5.4 budget throttle 4/hr/object (5th change defers via scheduled_at, never errors);
      budget edits always travel as their own set_budget job (split out of update)
- [x] P5.5 pause/activate inline, action_push (single record inline, batch via 5-min cron),
      push wizard w/ 3-column diff preview
- [x] P5.6 neutralize cancels queued jobs; conflict ribbon + resolve/push buttons on campaign
- [x] P5.7 tests — suite 87/87 green
- [x] P6.1 mixin draft flow (operation create) + per-model _validate_for_push/_create_push_payload
- [x] P6.2 Meta create campaign/adset/creative/ad (all PAUSED; creative auto-created from
      object_story_spec when the linked creative has no external id)
- [x] P6.3 Google atomic campaign create (temp -1 budget chaining, both resource names
      captured) + create ad_group; Google ad CREATION refused by design w/ clear message
- [x] P6.4 push job op create (writes back external_id/resource_name/budget_resource +
      creative back-link)
- [x] P6.5 views: Create-on-Platform buttons (confirm dialogs), readonly-after-push
      structural fields, special_ad_categories + targeting editable for drafts
- [x] P6.6 tests — suite 97/97 green
- [x] P7 audiences: ads_audience (pull pass w/ method-missing guard in engine), Meta
      custom-audience create + add_users (EMAIL/PHONE batches), Google userList +
      offlineUserDataJob (create→add→run), upload wizard (payload = partner IDS ONLY,
      hashing at push time via AdsApiBase.hash_pii, AUDIENCE_BATCH=10k)
- [x] P8 webhooks: /ads/meta/webhook (GET handshake + POST raw-body HMAC X-Hub-Signature-256,
      strict page match, stub-only fetch_pending leads drained by cron via get_lead),
      /ads/meta/data_deletion (signed_request verify), /ads/google/lead_webhook (google_key)
- [x] P9 conversion upload: crm.lead won hook (idempotent enqueue), job op conversion_upload,
      Google uploadClickConversions w/ partial_failure + Enhanced Conversions fallback
      (hash at push time), account fields + List Conversion Actions helper
- [x] P10 ads.performance.report SQL view (spend ⋈ CRM by utm campaign/month) + ROAS menu,
      store index.html, README complete, fresh `-i` verified — suite 120/120
- [x] Enterprise Social bridge shipped as ac_ads_connector_social (129/129 combined suite)
- [x] Store packaging: house-brand banner (1792x1024) + index.html regenerated via
      ._doc_program pipeline (added Marketing/MKT category, self-locating SCRATCH fix);
      manifest images restored
- [x] Arabic i18n: i18n/ar.po fully translated (490 terms, 0 empty), imported + verified
      (`odoo-bin i18n` subcommand — the old --i18n-export flag is GONE in 19; note `-l` is
      greedy nargs='+', put MODULE before -l)
- [ ] PENDING (post-v1): live smoke test w/ real credentials; store screenshots (needs a
      session with Playwright MCP — recipe in ._doc_program/PROGRESS.md); Meta CAPI;
      Google ad-content authoring; X/TikTok/LinkedIn providers; Meta structure delta
      filtering by updated_time (optimization)
- [ ] (deferred optimization) Meta structure delta filtering by updated_time

## Resume contract
Continue from the first unchecked P-item above. Model/field names in the plan file are
authoritative once a phase is done. Verify with:
`python3 /opt/odoo19/odoo/odoo-bin -c /opt/odoo19/odoo.conf -d ads_test -u ac_ads_connector --test-enable --test-tags /ac_ads_connector --stop-after-init`
Never point at a live DB. Restart the Odoo process after python changes. Do NOT commit/push — the user lands the work.

## Fixes during verification (Odoo 19 gotchas)
- `external_dependencies` python entries are **PyPI distribution names** in Odoo 19
  (`importlib.metadata.version`), not import names — `google.ads` won't even parse
  (PEP 508 forbids dots); use `facebook-business` / `google-ads`.
- Search views: no `string=` on `<group>` wrappers (RelaxNG rejects) — plain filters
  after a `<separator/>`.
- Never `write()` then `raise UserError` — the raise rolls the write back (both in real
  requests and under Odoo's savepoint-wrapped `assertRaises`). Test-connection failures
  write state and return a danger notification instead.
- `activity_schedule()` needs `mail.activity.mixin`, not just `mail.thread`.
- CLI `dropdb`/`psql` prompt for a password on this machine (peer auth off) — always
  `PGPASSWORD` from odoo.conf + `< /dev/null`, or a "10-minute install" is really a hung
  password prompt.

## Development Log
### 2026-08-11 — Odoo social* gap analysis (research, no code change)
- Audited all 15 `social*` modules in Odoo 19 EE + the 6 platform APIs they talk to.
  Report: `docs/odoo-social-gap-analysis.html` (422 capabilities; 82 implemented / 47 partial
  / 293 missing). Confirms: **zero advertising code anywhere in CE+EE** — no ad account,
  ad set, spend, audience, conversions API or lead form on any platform, so there is nothing
  for an "enterprise edition" to inherit on the ads side; `ac_ads_connector` stays the owner
  of the campaign → ad set → ad hierarchy.
- Reusable from `social` (already partly used by `_extend`): the Meta app credentials
  (`social.facebook_app_id` / `_client_secret` ICP keys), `instagram_facebook_account_id` as
  an IG↔Page map, the per-account `utm.medium` pattern, the OAuth-callback scaffolding.
  NOT reusable: `social.account.facebook_access_token` — it is a **Page** token and its scopes
  exclude `ads_read`/`ads_management`, so the Marketing API rejects it on token type alone.
- Adjacent risk logged for whoever owns the EE stack: `social_facebook`/`social_instagram`
  pin Graph **v17.0** (expired 2025-09-12); `social_linkedin` pins **202511** (sunsets
  2026-11-16) and stores a 60-day bearer token with no refresh path.

### 2026-08-10 (later) — Phase 1 complete
- Foundation verified: fresh install + update green on ads_test, 31/31 tests, 0 errors.
- action_test_connection reworked to notification-style failure (no UserError rollback).

### 2026-08-10
- Phase 0 complete: skill annotated with v26-era corrections; ac_social_ads absorbed (PLAN.md →
  docs/kimi-plan-reference.md); facebook-business 26.0.0 + google-ads 31.2.0 installed and
  smoke-imported in odoo-venv; module skeleton + manifest + requirements.txt; docs bootstrapped.
- Plan finalized after Kimi-plan review: name ac_ads_connector, official SDKs, standalone from
  enterprise social, ads.campaign _inherits utm.campaign (title = display name).
