# base_accounting_kit — Development Log

## Overview
Cybrosys accounting kit for Odoo 19 Community: financial reports, assets, PDC, bank
reconciliation, follow-ups, recurring invoices, lock dates. Deployed to the client database
`bethlehemcoop.com`.

## Current Status
**Status**: Complete (repo side) — production upgrade not yet run
**Last Updated**: 2026-09-21

## Stopping Point
Fixing schema/code drift on `bethlehemcoop.com`: the server runs module code at
`19.0.3.3.1` against a `19.0.2.3.0` schema because `-u base_accounting_kit` was never run
after commit `50e5e2f`. Missing `res_partner.followup_level_id` crashes the cron worker and
every flow that reads `res.partner`.

Full plan: `~/.claude/plans/odoo-open-pkg-2026-09-21-15-03-25-192-10-gentle-owl.md`

Repo side is done and verified locally. Two blockers were found and fixed; the upgrade then ran
clean and created every missing table and column. **Nothing has been run on the production
server** — the handover is `UPGRADE-bethlehemcoop.md` at the repo root.

## Next Steps / TODO
- [x] Install old state (`8512112`) into `oopkg_old`, snapshot with `pg_dump -Fc`
- [x] Control run: `-u` against unmodified HEAD — failed as predicted
- [x] Apply the `noupdate` fix in `data/account_financial_report_data.xml`
- [x] Verified run: `-u` with the fix, tables/columns created, log clean
- [x] Hand over the production runbook (`UPGRADE-bethlehemcoop.md`)
- [ ] Run the upgrade on `bethlehemcoop.com` (operator task, not done here)
- [ ] Commit the two fixes — currently uncommitted in the working tree

## Development Log
### 2026-09-21
- Diagnosed the `bethlehemcoop.com` log: the three `relation ... does not exist` errors are
  autovacuum noise; `column res_partner.followup_level_id does not exist` is the outage.
- Confirmed a manifest version bump never triggers a schema update
  (`ir_module.py:809` only counts it), so `-u` is the only fix.
- Found an upgrade blocker: `account_report_view_form` is a view sitting inside a
  `noupdate="1"` block, and HEAD repoints it from `account.report` to `account.common.report`.
  The arch is skipped on upgrade but still re-validated
  (`loading.py:246` -> `ir_ui_view.py:2588`), so validation raises on the stale arch.
- Set up the local reproduction: worktree at `8512112`, DB `oopkg_old`. The baseline matches
  production exactly — module at `19.0.2.3.0`, none of the new tables, no
  `res_partner.followup_level_id`, and `account_report_view_form` stored as `account.report`
  with `noupdate = true`.
- **Control run proved the blocker.** `-u base_accounting_kit` against unmodified HEAD aborts
  with `ValidationError: Field "target_move" does not exist in model "account.report"` and
  rolls back completely (module still reads `19.0.2.3.0 | installed` afterwards). So the
  upgrade as it stands today would fail on production and fix nothing.
- The local repro must exclude `/opt/odoo19/enterprise` from `addons_path`. Enterprise
  `account_reports` adds `horizontal_group_ids` to `account.report`
  (`account_reports/models/account_report.py:68`), and the OLD module code prototypes that
  model into `account.common.journal.report`, producing a relation table name over
  Postgres' 63-character limit. Production is Community, so this is a harness detail only —
  and HEAD removes the `account.report` inherit, so it no longer applies there either.
- Applied the fix: moved `account_report_view_form` out of the `noupdate="1"` block in
  `data/account_financial_report_data.xml` into a plain `<data>` block.
- **Verified the full fix.** Code fix + the one-row `noupdate` SQL upgraded a restored baseline
  to `19.0.3.3.1 | installed` with zero ERROR/CRITICAL lines. All seven tables and all nine
  columns were created, including `res_partner.followup_level_id`. The view is now stored as
  `account.common.report` with `noupdate = false`, and its arch carries the new Export XLSX
  button (absent before).
- **The code fix alone is not enough** — tested separately and it fails identically to the
  control. `_load_records` reads `noupdate` from the **database** row (`models.py:5178`), not
  from the XML, so an already-installed database needs the SQL too. The code fix is what stops
  this recurring on every other deployment.
- Second blocker, introduced by commit `77e70e3` ("remove", 18:51): it deleted the
  `cybrosys_support_client` directory but left `'cybrosys_support_client'` in this module's
  `depends`. Any install or upgrade then dies with *"You try to upgrade the module
  base_accounting_kit that depends on the module: cybrosys_support_client. But this module is
  not available in your system."* (`ir_module.py:744`). Completed the removal by dropping the
  entry from `depends`.
