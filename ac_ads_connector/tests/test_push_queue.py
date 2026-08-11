# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module
from odoo.addons.ac_ads_connector.tools.ads_api import AdsApiError
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi
from odoo.addons.ac_ads_connector.tools.meta_api import MetaAdsApi

from .common import AdsCommon
from .test_sync_engine import FakeStructureApi, _campaign_row


class FakePushApi(FakeStructureApi):
    calls = []
    raise_error = None

    def _maybe_raise(self):
        if type(self).raise_error:
            raise type(self).raise_error

    def set_status(self, object_type, record, status):
        self._maybe_raise()
        type(self).calls.append(("set_status", object_type, record.external_id, status))

    def set_budget(self, object_type, record, budget_type, amount):
        self._maybe_raise()
        type(self).calls.append(("set_budget", object_type, record.external_id,
                                 budget_type, amount))

    def update_object(self, object_type, record, values, changed):
        self._maybe_raise()
        type(self).calls.append(("update", object_type, record.external_id,
                                 dict(values), list(changed)))


@tagged("post_install", "-at_install")
class TestPushQueue(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env["ads.sync.engine"]
        cls.meta_account.state = "connected"
        cls.engine._upsert_page(cls.meta_account, "ads.campaign",
                                [_campaign_row("c1", "Push Camp")])
        cls.campaign = cls.env["ads.campaign"].search([("external_id", "=", "c1")])

    def setUp(self):
        super().setUp()
        FakePushApi.calls = []
        FakePushApi.raise_error = None
        self.patcher = patch.dict(account_module.PROVIDER_API, {"meta": FakePushApi})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    # --------------------------------------------------------- state machine
    def test_local_edit_flips_to_local_changes(self):
        self.assertEqual(self.campaign.sync_status, "synced")
        self.campaign.write({"title": "Edited Locally"})
        self.assertEqual(self.campaign.sync_status, "local_changes")
        # non-pushable edits leave the state alone
        self.campaign.with_context(skip_ads_sync=True).sync_status = "synced"
        self.campaign.write({"objective_raw": "SOMETHING"})
        self.assertEqual(self.campaign.sync_status, "synced")

    def test_pull_does_not_dirty(self):
        self.engine._upsert_page(self.meta_account, "ads.campaign",
                                 [_campaign_row("c1", "Remote Rename")])
        self.assertEqual(self.campaign.sync_status, "synced")
        self.assertEqual(self.campaign.title, "Remote Rename")

    def test_action_push_runs_update(self):
        self.campaign.write({"title": "Edited", "bid_strategy": "COST_CAP"})
        self.campaign.action_push()
        self.assertEqual(self.campaign.sync_status, "synced")
        self.assertTrue(self.campaign.last_pushed_snapshot)
        kinds = [c[0] for c in FakePushApi.calls]
        self.assertEqual(kinds, ["update"])
        _kind, object_type, ext, values, changed = FakePushApi.calls[0]
        self.assertEqual((object_type, ext), ("campaign", "c1"))
        self.assertNotIn("budget_amount", values)
        job = self.env["ads.push.job"].search([("res_id", "=", self.campaign.id)])
        self.assertEqual(job.state, "done")

    def test_budget_split_into_throttled_job(self):
        self.campaign.write({"title": "Edited", "budget_amount": 123.0})
        self.campaign.action_push()
        kinds = sorted(c[0] for c in FakePushApi.calls)
        self.assertEqual(kinds, ["set_budget", "update"])
        budget_call = next(c for c in FakePushApi.calls if c[0] == "set_budget")
        self.assertEqual(budget_call[4], 123.0)
        update_call = next(c for c in FakePushApi.calls if c[0] == "update")
        self.assertNotIn("budget_amount", update_call[3])

    def test_pause_activate_inline(self):
        self.campaign.action_pause()
        self.assertEqual(self.campaign.status, "paused")
        self.assertEqual(self.campaign.sync_status, "synced")
        self.campaign.action_activate()
        self.assertEqual(self.campaign.status, "enabled")
        self.assertEqual(FakePushApi.calls[0][3], "paused")
        self.assertEqual(FakePushApi.calls[1][3], "enabled")

    # -------------------------------------------------------------- conflicts
    def test_conflict_detection_and_resolution(self):
        self.campaign.write({"title": "Local Edit"})
        self.engine._upsert_page(self.meta_account, "ads.campaign",
                                 [_campaign_row("c1", "Remote Edit",
                                                budget_amount=77.0)])
        self.assertEqual(self.campaign.sync_status, "conflict")
        self.assertEqual(self.campaign.title, "Local Edit")
        snapshot = self.campaign.remote_snapshot
        self.assertEqual(snapshot.get("title"), "Remote Edit")
        # keep remote applies the snapshot
        self.campaign.action_resolve_keep_remote()
        self.assertEqual(self.campaign.sync_status, "synced")
        self.assertEqual(self.campaign.title, "Remote Edit")
        self.assertEqual(self.campaign.budget_amount, 77.0)

    def test_conflict_keep_local_repushes(self):
        self.campaign.write({"title": "Local Edit"})
        self.engine._upsert_page(self.meta_account, "ads.campaign",
                                 [_campaign_row("c1", "Remote Edit")])
        self.assertEqual(self.campaign.sync_status, "conflict")
        self.campaign.action_resolve_keep_local()
        self.assertEqual(self.campaign.sync_status, "synced")
        self.assertEqual(self.campaign.title, "Local Edit")
        self.assertIn("update", [c[0] for c in FakePushApi.calls])

    def test_metadata_only_remote_change_is_not_conflict(self):
        self.campaign.write({"title": "Local Edit"})
        # remote row identical on every pushable field -> no conflict
        row = _campaign_row("c1", "Local Edit")
        self.engine._upsert_page(self.meta_account, "ads.campaign", [row])
        self.assertEqual(self.campaign.sync_status, "local_changes")

    # --------------------------------------------------------------- failures
    def test_recoverable_failure_requeues_with_backoff(self):
        FakePushApi.raise_error = AdsApiError("later", "recoverable")
        self.campaign.write({"title": "Edited"})
        self.campaign.action_push()
        job = self.env["ads.push.job"].search([("res_id", "=", self.campaign.id)])
        self.assertEqual(job.state, "queued")
        self.assertEqual(job.attempt, 1)
        self.assertGreater(job.scheduled_at, fields.Datetime.now())
        self.assertEqual(self.campaign.sync_status, "queued")

    def test_validation_failure_is_terminal(self):
        FakePushApi.raise_error = AdsApiError("bad", "validation")
        self.campaign.write({"title": "Edited"})
        self.campaign.action_push()
        job = self.env["ads.push.job"].search([("res_id", "=", self.campaign.id)])
        self.assertEqual(job.state, "error")
        self.assertEqual(self.campaign.sync_status, "push_error")
        self.assertIn("bad", self.campaign.push_error_message)
        # manual retry re-queues
        FakePushApi.raise_error = None
        job.button_retry()
        self.assertEqual(job.state, "queued")

    def test_auth_failure_flags_account(self):
        FakePushApi.raise_error = AdsApiError("dead token", "auth")
        self.campaign.write({"title": "Edited"})
        self.campaign.action_push()
        self.assertEqual(self.meta_account.state, "error")

    def test_cooldown_defers_job(self):
        self.meta_account._set_cooldown(600, "test")
        self.campaign.write({"title": "Edited"})
        self.campaign.action_push()
        job = self.env["ads.push.job"].search([("res_id", "=", self.campaign.id)])
        self.assertEqual(job.state, "queued")
        self.assertEqual(job.scheduled_at, self.meta_account.cooldown_until)
        self.assertFalse(FakePushApi.calls)

    def test_budget_throttle_defers_fifth_change(self):
        Job = self.env["ads.push.job"]
        for _i in range(4):
            job = Job._enqueue(self.campaign, "set_budget", {
                "object_type": "campaign", "external_id": "c1",
                "budget_type": "daily", "amount": 10.0})
            job._run_one()
            self.assertEqual(job.state, "done")
        fifth = Job._enqueue(self.campaign, "set_budget", {
            "object_type": "campaign", "external_id": "c1",
            "budget_type": "daily", "amount": 99.0})
        fifth._run_one()
        self.assertEqual(fifth.state, "queued")
        self.assertGreater(fifth.scheduled_at,
                           fields.Datetime.now() + timedelta(minutes=50))
        self.assertEqual(len([c for c in FakePushApi.calls
                              if c[0] == "set_budget"]), 4)

    # ------------------------------------------------------- provider payloads
    def test_meta_push_params(self):
        api = MetaAdsApi(self.meta_account)
        params = api._push_params("campaign", {
            "title": "New Name", "status": "paused",
            "start_datetime": "2026-08-10 08:00:00",
            "budget_amount": 55.0, "bid_strategy": "COST_CAP",
        }, ["title", "status", "start_datetime", "budget_amount", "bid_strategy"])
        self.assertEqual(params["name"], "New Name")
        self.assertEqual(params["status"], "PAUSED")
        self.assertEqual(params["start_time"], "2026-08-10T08:00:00+0000")
        self.assertEqual(params["bid_strategy"], "COST_CAP")
        self.assertNotIn("budget_amount", params)
        self.assertNotIn("daily_budget", params)

    def test_google_update_mask(self):
        api = GoogleAdsApi(self.google_account)
        client = MagicMock()
        client.get_type.side_effect = lambda name: MagicMock
        operations = []

        def capture(name):
            op = MagicMock()
            operations.append(op)
            return lambda: op

        client.get_type.side_effect = capture
        with patch.object(GoogleAdsApi, "_build_sdk", return_value=client):
            self.campaign.provider_data = {"resource_name": "customers/1/campaigns/42"}
            api.update_object("campaign", self.campaign,
                              {"title": "G Name", "status": "paused",
                               "stop_datetime": "2026-09-01 00:00:00"},
                              ["title", "status", "stop_datetime"])
        operation = operations[-1]
        operation.update_mask.paths.extend.assert_called_once()
        paths = operation.update_mask.paths.extend.call_args[0][0]
        self.assertEqual(paths, ["name", "status", "end_date"])
        self.assertEqual(operation.update.name, "G Name")
        self.assertEqual(operation.update.end_date, "2026-09-01")
        service = client.get_service.return_value
        service.mutate_campaigns.assert_called_once()
        self.assertEqual(service.mutate_campaigns.call_args.kwargs["customer_id"],
                         "1234567890")

    def test_google_ad_content_immutable(self):
        api = GoogleAdsApi(self.google_account)
        client = MagicMock()
        with patch.object(GoogleAdsApi, "_build_sdk", return_value=client):
            # name is not pushable for google ads -> no mutate call at all
            api.update_object("ad", self.campaign, {"name": "X"}, ["name"])
        client.get_service.assert_not_called()

    def test_google_budget_requires_resource(self):
        api = GoogleAdsApi(self.google_account)
        record = MagicMock()
        record.google_budget_resource = False
        with self.assertRaises(AdsApiError) as ctx:
            api.set_budget("campaign", record, "daily", 10.0)
        self.assertEqual(ctx.exception.failure_type, "validation")
