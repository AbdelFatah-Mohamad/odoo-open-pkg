# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import AdsCommon


@tagged("post_install", "-at_install")
class TestLinkTracker(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.campaign = cls.env["ads.campaign"].create({
            "title": "LT Camp", "account_id": cls.meta_account.id,
            "external_id": "lt1", "sync_status": "synced"})
        cls.adset = cls.env["ads.adset"].create({
            "name": "LT Set", "campaign_id": cls.campaign.id,
            "external_id": "lts1", "sync_status": "synced"})
        cls.creative = cls.env["ads.creative"].create({
            "name": "LT OSS", "account_id": cls.meta_account.id,
            "object_story_spec": {"page_id": "424242",
                                  "link_data": {"link": "https://alshayeb.ps/lp"}}})
        cls.ad = cls.env["ads.ad"].create({
            "name": "LT Ad", "adset_id": cls.adset.id,
            "creative_id": cls.creative.id, "sync_status": "synced",
            "external_id": "lta1"})

    def test_create_and_reuse_tracker(self):
        self.ad.action_create_tracked_link()
        tracker = self.ad.link_tracker_id
        self.assertTrue(tracker)
        self.assertEqual(tracker.url, "https://alshayeb.ps/lp")
        self.assertEqual(tracker.campaign_id, self.campaign.utm_campaign_id)
        self.assertTrue(tracker.short_url)
        self.assertIn("/r/", tracker.short_url)
        # second call: search_or_create reuses the same tracker
        self.ad.action_create_tracked_link()
        self.assertEqual(self.ad.link_tracker_id, tracker)
        self.assertEqual(self.env["link.tracker"].search_count(
            [("url", "=", "https://alshayeb.ps/lp"),
             ("label", "=", "LT Ad")]), 1)

    def test_no_url_raises(self):
        bare = self.env["ads.ad"].create({
            "name": "No URL", "adset_id": self.adset.id, "sync_status": "synced",
            "external_id": "lta2"})
        with self.assertRaises(UserError):
            bare.action_create_tracked_link()

    def test_campaign_rollup_and_action(self):
        self.ad.action_create_tracked_link()
        self.env["link.tracker.click"].create({
            "link_id": self.ad.link_tracker_id.id, "ip": "127.0.0.1"})
        self.ad.link_tracker_id.invalidate_recordset()
        self.assertEqual(self.ad.click_count, 1)
        self.campaign.invalidate_recordset()
        self.assertEqual(self.campaign.tracked_click_count, 1)
        action = self.campaign.action_view_tracked_links()
        self.assertEqual(action["domain"][0][2], self.ad.link_tracker_id.ids)

    def test_tracked_url_swapped_on_draft_push(self):
        self.meta_account.use_tracked_urls = True
        draft = self.env["ads.ad"].create({
            "name": "Draft Tracked", "adset_id": self.adset.id,
            "creative_id": self.creative.id, "sync_status": "draft"})
        payload = draft._create_push_payload()
        link = payload["values"]["creative"]["object_story_spec"]["link_data"]["link"]
        self.assertEqual(link, draft.link_tracker_id.short_url)
        self.assertIn("/r/", link)
        # opt-out path keeps utm tagging instead
        self.meta_account.use_tracked_urls = False
        plain = self.env["ads.ad"].create({
            "name": "Draft Plain", "adset_id": self.adset.id,
            "creative_id": self.creative.id, "sync_status": "draft"})
        payload = plain._create_push_payload()
        link = payload["values"]["creative"]["object_story_spec"]["link_data"]["link"]
        self.assertIn("utm_campaign=", link)
        self.assertNotIn("/r/", link)
