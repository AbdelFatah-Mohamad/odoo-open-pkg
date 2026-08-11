# -*- coding: utf-8 -*-
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import AdsCommon


@tagged("post_install", "-at_install")
class TestSecurity(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Ads User",
            "login": "ads_user",
            "group_ids": [Command.set([
                cls.env.ref("base.group_user").id,
                cls.env.ref("ac_ads_connector.group_ads_user").id,
            ])],
        })

    def test_user_cannot_read_credentials(self):
        account = self.meta_account.with_user(self.user)
        self.assertEqual(account.name, "Meta Test")  # base read is fine
        with self.assertRaises(AccessError), mute_logger("odoo.models"):
            account.read(["meta_access_token"])

    def test_user_cannot_write_accounts(self):
        with self.assertRaises(AccessError), mute_logger("odoo.addons.base.models.ir_rule",
                                                         "odoo.models"):
            self.meta_account.with_user(self.user).write({"name": "hack"})

    def test_multi_company_rule(self):
        other_company = self.env["res.company"].create({"name": "Other Co"})
        hidden = self.env["ads.account"].create({
            "name": "Hidden", "provider": "meta",
            "external_account_id": "777", "company_id": other_company.id,
        })
        visible = self.env["ads.account"].with_user(self.user).search([])
        self.assertNotIn(hidden.id, visible.ids)
        self.assertIn(self.meta_account.id, visible.ids)

    def test_neutralize_wipes_credentials(self):
        module_path = __file__.rsplit("/tests/", 1)[0]
        with open(f"{module_path}/data/neutralize.sql") as handle:
            script = handle.read()
        self.env.flush_all()
        self.env.cr.execute(script)
        self.env.invalidate_all()
        account = self.meta_account.sudo()
        self.assertFalse(account.meta_access_token)
        self.assertFalse(account.meta_app_secret)
        self.assertEqual(account.state, "draft")
        google = self.google_account.sudo()
        self.assertFalse(google.google_refresh_token)
        self.assertFalse(google.google_developer_token)

    def test_unique_external_account(self):
        from psycopg2 import errors  # noqa: PLC0415
        with self.assertRaises(Exception) as ctx, mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self.env["ads.account"].create({
                    "name": "Dup", "provider": "meta",
                    "external_account_id": "123456789",
                    "company_id": self.meta_account.company_id.id,
                })
        self.assertIsInstance(ctx.exception, errors.UniqueViolation)
