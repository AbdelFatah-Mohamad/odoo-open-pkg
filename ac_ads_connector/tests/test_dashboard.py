# -*- coding: utf-8 -*-
import json

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAdsDashboard(TransactionCase):

    def test_dashboard_record_and_payload(self):
        dashboard = self.env.ref(
            "ac_ads_connector.spreadsheet_dashboard_ads")
        self.assertEqual(dashboard.name, "Ads Performance")
        self.assertTrue(dashboard.is_published)
        self.assertIn(self.env.ref("ac_ads_connector.model_ads_insight"),
                      dashboard.main_data_model_ids)
        self.assertIn(self.env.ref("ac_ads_connector.group_ads_user"),
                      dashboard.group_ids)
        data = json.loads(dashboard.spreadsheet_data)
        self.assertEqual(data["revisionId"], "START_REVISION")
        figures = data["sheets"][0]["figures"]
        self.assertEqual(len(figures), 6)
        Insight = self.env["ads.insight"]
        for figure in figures:
            meta = figure["data"]["metaData"]
            self.assertEqual(meta["resModel"], "ads.insight")
            self.assertIn(meta["measure"], Insight._fields,
                          f"unknown measure {meta['measure']}")
            for group in meta["groupBy"]:
                field_name = group.split(":")[0]
                self.assertIn(field_name, Insight._fields,
                              f"unknown groupBy {field_name}")
            for leaf in figure["data"]["searchParams"]["domain"]:
                if isinstance(leaf, (list, tuple)):
                    self.assertIn(leaf[0], Insight._fields)

    def test_group_created(self):
        group = self.env.ref(
            "ac_ads_connector.spreadsheet_dashboard_group_ads")
        self.assertIn(self.env.ref(
            "ac_ads_connector.spreadsheet_dashboard_ads"),
            group.dashboard_ids)
