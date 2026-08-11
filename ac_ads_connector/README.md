# AC Ads Connector — Meta & Google Ads for Odoo 19

Connect Odoo to **Meta (Facebook/Instagram) Ads** and **Google Ads**: mirror campaigns, ad sets,
ads and creatives; pull daily insights; land lead-form leads in CRM with attribution and duplicate
detection; manage status/budgets and author campaigns from Odoo; upload won leads back to Google
as conversions.

## Requirements

- Odoo 19 (Community or Enterprise). Depends on `crm`, `utm`, `mail` only.
- Python packages in the Odoo venv (see `requirements.txt`):
  `pip install "facebook-business>=26,<27" "google-ads>=31,<32"`

## Credentials setup

### Meta (Facebook/Instagram)

1. On developers.facebook.com create a **Business-type app** and add the **Marketing API** and
   **Webhooks** products.
2. In Business Settings → **System Users**: create a system user, assign your **ad accounts** and
   **Pages** to it, and generate a token with scopes:
   `ads_read, ads_management, leads_retrieval, pages_manage_metadata, pages_show_list,
   pages_read_engagement, pages_manage_ads`.
3. Optional but recommended for lead retrieval at scale: a **Page access token** per Page
   (lead-endpoint rate limits are counted per Page).
4. Security: enable **App Secret Proof** in the app's Advanced settings; paste the App Secret into
   the Odoo account record (used for `appsecret_proof` and webhook signatures).
5. Production leads require **App Review** for `leads_retrieval` (+ `pages_manage_ads`) and
   **Business Verification**. Start this early — it gates live lead retrieval, and Full/Standard
   API access additionally requires ≥500 calls in 15 days with <15% errors. Development ("Limited")
   access works for building and testing (~600 calls/hour).

### Google Ads

1. In your **Manager (MCC) account** → API Center: request a **developer token**. It works on test
   accounts immediately; apply for **Basic Access** right away (~5 business days, 15,000 ops/day).
2. In Google Cloud Console: create a project, enable the **Google Ads API**, configure the OAuth
   consent screen, and create an **OAuth client id/secret** — then use the *Connect Google* wizard
   on the Odoo account record to run the one-time consent flow (scope `adwords`) and store the
   refresh token.
   **Or** use a **service account**: create it, download the JSON key, and add the service-account
   email **as a user of the Google Ads account** in the Google Ads UI (no domain-wide delegation /
   impersonation is needed).
3. Record your `login customer id` (the MCC id, digits only) when access goes through a manager
   account.
4. Optional real-time leads: in the Google Ads UI, configure the **lead form webhook** to POST to
   `https://<your-odoo-host>/ads/google/lead_webhook` with the key shown on the Odoo account form.

## Webhooks (Meta real-time leads)

Callback URL: `https://<your-odoo-host>/ads/meta/webhook` with the verify token shown on the
account form. Subscribe the **Page** to the `leadgen` field and install the app on the Page
(`POST /{page-id}/subscribed_apps?subscribed_fields=leadgen`). Requires public HTTPS; polling
remains active as a reconciliation net, so missed webhooks are recovered automatically.

## Audiences

Create customer-list audiences (Meta Custom Audience / Google Customer Match user list) from
**Ads → Configuration → Audiences**, then upload members from your contacts. Emails and phone
numbers are normalized and SHA-256 hashed at upload time; hashes are never stored in Odoo.
Google customer match requires an account in good standing (policy gated by Google).

## Conversion upload (Google)

Enable **Conversion upload** on the Google account, pick an `UPLOAD_CLICKS` conversion action
(the *List Conversion Actions* button prints the available ones in the chatter). From then on,
every CRM lead that reaches a Won stage and is attributed to a Google lead uploads a click
conversion — by `gclid` when available, otherwise via Enhanced Conversions for Leads (hashed
email/phone, hashed at push time only).

## Two modules, one product

- **`ac_ads_connector`** (this module) — everything that works on Community: the full
  platform sync, leads→CRM, write-back, audiences, conversion upload, `gclid`/`fbclid`
  click-ID capture on website leads, UTM auto-tagging and tracked short links, ROAS/CPL
  report, and the built-in native spreadsheet dashboard.
- **`ac_ads_connector_extend`** — install it deliberately on Enterprise databases: it pulls
  Sales+CRM, Social and Marketing Automation with it and adds real confirmed-order revenue
  and ROAS per campaign, organic reach next to paid performance (linked by Facebook Page,
  optional shared UTM medium), instant Marketing Automation intake for ad leads, and a
  ready-made "Welcome Ad Leads" nurture template.

## Not included (by design)

- Meta Conversions API (CAPI) uploads — future work (Google click-conversion upload IS included).
- X (Twitter) Ads — API frozen at v12 since 2022 with no lead retrieval; the provider architecture
  accepts it later if it revives.
- TikTok / LinkedIn / Snapchat providers — planned as future drop-ins.
- Organic social posting — that is Odoo's enterprise Social app; this module is ads-only and does
  not depend on it.

## Security notes

- All tokens/secrets are admin-group-only fields and are wiped by `data/neutralize.sql` on database
  neutralization (restored copies can never touch live ad money).
- Webhooks verify HMAC signatures over the raw request body; unknown or unsigned deliveries are
  rejected with 403 and logged.
