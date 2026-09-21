# Runbook — upgrading `base_accounting_kit` on bethlehemcoop.com

**Audience:** whoever has shell and psql access on the server hosting the `bethlehemcoop.com`
database. Every command below runs on that host.

## Why this is needed

The server runs `base_accounting_kit` **code** at `19.0.3.3.1` while the database **schema** is
still at `19.0.2.3.0`. A manifest version bump does not create schema — only `-u` does — so the
tables and columns added in commit `50e5e2f` were never created.

The visible failure is:

```
column res_partner.followup_level_id does not exist
Worker WorkerCron (1054) Exception occurred, exiting...
```

This is not a cron bug. `ir_cron` reads `env.tz`, which reads `res.users.tz`, which is inherited
from `res.partner`, which makes the ORM SELECT every stored partner column. The missing column
therefore breaks **anything that touches `res.partner`**, not just scheduled actions. The cron
worker crash-loops on respawn.

The three `relation "asset_dispose" / "asset_revaluation" / "account_fx_revaluation" does not
exist` errors are harmless noise — `ir_autovacuum` catches and rolls back per method.

The fix is one `-u base_accounting_kit`. Two blockers had to be fixed in the repo first; both
are committed, so **pull before upgrading**.

## Verified before writing this

Reproduced on a throwaway local database built from commit `8512112` (the pre-`50e5e2f` state),
which matched production exactly: module at `19.0.2.3.0`, none of the new tables, no
`res_partner.followup_level_id`, `account_report_view_form` stored as `account.report` with
`noupdate = true`.

| Run | Setup | Result |
|---|---|---|
| Control | unmodified HEAD | **Failed.** `ValidationError: Field "target_move" does not exist in model "account.report"`, full rollback, module still `19.0.2.3.0`. |
| Code fix only | view moved out of the `noupdate` block | **Failed identically.** The stored `noupdate` flag wins — `_load_records` reads it from the database, not the XML. |
| Code fix + SQL | both | **Clean.** `19.0.3.3.1 | installed`, zero ERROR/CRITICAL lines. |

Both steps are required. Neither alone works.

## Pre-flight (read-only — do this before touching anything)

1. **Python dependency.** `xlsxwriter` was added to `external_dependencies` in `50e5e2f`, the
   commit already on the server, and it is checked *before* any upgrade work begins.
   ```bash
   /path/to/prod/venv/bin/python -c "import xlsxwriter, openpyxl, ofxparse, qifparse; print('ok')"
   ```
   A missing wheel aborts the upgrade with a `UserError`. Install it first if needed.

2. **What else gets dragged in.** `button_upgrade` cascades to *dependents*, so every installed
   module that depends on `base_accounting_kit` is upgraded too. Confirm the list holds no
   surprises:
   ```sql
   SELECT name, latest_version, state FROM ir_module_module
    WHERE state <> 'uninstalled' ORDER BY name;
   ```

3. **Asset depreciation backlog** — decides whether step 6 below is merely precautionary or
   essential:
   ```sql
   SELECT count(*), min(l.depreciation_date), max(l.depreciation_date)
     FROM account_asset_depreciation_line l JOIN account_asset_asset a ON a.id = l.asset_id
    WHERE a.state='open' AND l.move_check=false AND l.depreciation_date <= CURRENT_DATE;
   ```

4. **Check numbers.** `print_checks` now casts `check_number::BIGINT` in raw SQL, so a
   non-numeric value breaks check printing after the upgrade:
   ```sql
   SELECT DISTINCT check_number FROM account_payment WHERE check_number !~ '^[0-9]*$';
   ```

5. **Custom child views.** The upgrade deletes two view xmlids (`view_partner_form` from
   `credit_limit_view.xml`, `product_template_form_view` from the now-deleted
   `product_template_views.xml`). `inherit_id` is `ondelete='cascade'`, so any Studio or custom
   view inheriting them is deleted with them:
   ```sql
   SELECT v.id, v.name FROM ir_ui_view v WHERE v.inherit_id IN (
     SELECT res_id FROM ir_model_data WHERE module='base_accounting_kit' AND model='ir.ui.view'
       AND name IN ('view_partner_form','product_template_form_view'));
   ```

## Execution

1. **Stop the Odoo service.** Never run `-u` against live workers — it is a registry-changing
   transaction.

2. **Back up.** `pg_dump -Fc` the database *and* tar the filestore. Confirm the dump is non-zero
   and restorable before going further.

3. **Pull the code.** The two blocker fixes must be present.
   ```bash
   cd /opt/odoo/repos/<this repo> && git pull
   ```

4. **Clear the one stale `noupdate` flag.** Exactly this row, nothing wider — a blanket clear
   would overwrite the customer's financial report tree, PDC payment methods and follow-up
   levels, which are all correctly protected:
   ```sql
   UPDATE ir_model_data SET noupdate = false
    WHERE module='base_accounting_kit' AND name='account_report_view_form';
   ```

5. **Upgrade.**
   ```bash
   odoo-bin -c <conf> -d bethlehemcoop.com -u base_accounting_kit --stop-after-init
   ```
   Optionally add `,base_account_budget` for its `19.0.1.0.0 → 19.0.1.0.1` bump — a three-line
   change with no new fields, so it carries no drift and is not required.

6. **Deactivate the two newly created active crons, before starting the service.**
   ```sql
   UPDATE ir_cron SET active = false WHERE id IN (
     SELECT res_id FROM ir_model_data WHERE module='base_accounting_kit'
       AND name IN ('account_asset_cron','account_recurring_invoice_cron'));
   ```
   `account_asset_cron` runs `_cron_generate_entries()`, which selects **every** depreciation
   line with `depreciation_date <= today AND move_check = false` on open assets and calls
   `action_post()`. On a database where depreciation was only ever posted manually, its first
   tick posts the entire historical backlog, back-dated, possibly into closed periods.

7. **Start the service.**

## Post-checks

- Log boots clean, no `UndefinedColumn`.
- Open a customer form — the record type that was crashing.
- Cron worker survives a full cycle without exiting.
- Schema:
  ```sql
  SELECT tablename FROM pg_tables WHERE tablename IN
   ('asset_dispose','asset_revaluation','account_fx_revaluation','account_bulk_payment',
    'account_recurring_invoice','account_recurring_invoice_line','account_common_report');
  SELECT count(*) FROM information_schema.columns
   WHERE table_name='res_partner' AND column_name='followup_level_id';
  ```
  Expect seven rows and `1`.
- Accounting → Reports → Trial Balance shows an **Export XLSX** button. Its absence means the
  view was not rewritten, i.e. step 4 was skipped.

## Watch afterwards

- **`recurring_template_cron` ("Generate Recurring Entries") is already active today** and stays
  active — it is not one of the two above. Its behaviour changes with this upgrade: entries now
  take `company_id` from the template rather than the current company, carry the template's
  `analytic_distribution`, and post via `action_post()`. Check the first batch it produces.
- **`ir_cron_send_followups` and `ir_cron_bank_auto_reconcile` ship inactive.** Leave them that
  way until configured. `_cron_auto_reconcile` in particular reconciles every unreconciled bank
  statement line database-wide, with no date or journal bound.
- **`followup.line.send_email` defaults to `True`**, so existing follow-up levels come out of the
  upgrade with "Send an Email" armed. Nothing sends on its own while the follow-up cron is off,
  but the manual button sends immediately.
- **The new persistent models** (`account.bulk.payment`, `account.recurring.invoice`) have ACLs
  but no `ir.rule`. On a multi-company database their records are visible across companies.

## Rollback

Restore the `pg_dump -Fc` backup and the filestore tarball, then `git checkout` the previous
commit. The upgrade is a single transaction: if it fails it rolls back on its own and leaves the
module at `19.0.2.3.0`, exactly as the control run showed.
