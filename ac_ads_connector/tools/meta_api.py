# -*- coding: utf-8 -*-
"""Meta (Facebook/Instagram) Marketing API client over the official
``facebook_business`` SDK.

Design constraints honoured here:

  * **Per-account API instance, never global**: ``FacebookAdsApi.init()`` sets a
    process-wide default which would leak tokens across ``ads.account`` records
    running in the same worker. Every AdObject we build gets ``api=self.sdk``
    explicitly.
  * **appsecret_proof**: passing ``app_secret`` into ``FacebookSession`` makes
    the SDK sign every call automatically.
  * **Version pinning**: the Graph version is an account field
    (``meta_api_version``, default v26.0) — never the SDK default.
  * Usage/rate signals arrive on response AND error headers
    (``X-App-Usage``, ``X-Business-Use-Case-Usage``,
    ``X-FB-Ads-Insights-Throttle``); parsed into a persisted cooldown.
"""
import json
import logging
from datetime import timedelta

import requests as _requests_lib

from facebook_business.api import FacebookAdsApi
from facebook_business.session import FacebookSession
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adreportrun import AdReportRun
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.customaudience import CustomAudience
from facebook_business.adobjects.lead import Lead
from facebook_business.adobjects.leadgenform import LeadgenForm
from facebook_business.adobjects.page import Page
from facebook_business.exceptions import FacebookRequestError

from .ads_api import AdsApiBase, AdsApiError

_logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v26.0"
PAGE_SIZE = 100

#: Meta error code -> failure_type (codes not listed fall through to the
#: status-based default). Sources: Graph API error reference, BUC rate limiting.
_AUTH_CODES = {190, 102, 463, 467}
_PERMISSION_CODES = {3, 10, 200, 294}  # 200-299 handled by range check too
_RATE_CODES = {4, 17, 32, 613, 80000, 80003, 80004, 80014}
_TOO_MUCH_DATA = (100, 1487534)  # (code, subcode) on insights

#: usage-percentage threshold that triggers a defensive cooldown.
_USAGE_SOFT_LIMIT = 95
_SOFT_COOLDOWN = 300  # seconds

#: Meta *configured* status -> unified vocabulary (effective_status is kept
#: verbatim in platform_status).
_STATUS_MAP = {
    "ACTIVE": "enabled",
    "PAUSED": "paused",
    "DELETED": "removed",
    "ARCHIVED": "archived",
}

_OBJECTIVE_MAP = {
    "OUTCOME_AWARENESS": "awareness",
    "OUTCOME_TRAFFIC": "traffic",
    "OUTCOME_ENGAGEMENT": "engagement",
    "OUTCOME_LEADS": "leads",
    "OUTCOME_APP_PROMOTION": "app",
    "OUTCOME_SALES": "sales",
}

META_CAMPAIGN_FIELDS = [
    "id", "name", "status", "effective_status", "objective", "buying_type",
    "daily_budget", "lifetime_budget", "bid_strategy", "special_ad_categories",
    "start_time", "stop_time", "updated_time",
]
META_ADSET_FIELDS = [
    "id", "name", "campaign_id", "status", "effective_status", "daily_budget",
    "lifetime_budget", "optimization_goal", "billing_event", "targeting",
    "promoted_object", "start_time", "end_time", "updated_time",
]
META_AD_FIELDS = [
    "id", "name", "adset_id", "campaign_id", "status", "effective_status",
    "preview_shareable_link", "updated_time",
    "creative{id,name,title,body,call_to_action_type,image_url,thumbnail_url,"
    "object_story_spec}",
]

_INSIGHT_BASE_FIELDS = ["spend", "impressions", "clicks", "reach", "actions",
                        "action_values", "date_start"]
#: id fields available per insights level (requesting an unavailable one -> #100).
_INSIGHT_LEVEL_IDS = {
    "account": [],
    "campaign": ["campaign_id"],
    "adset": ["campaign_id", "adset_id"],
    "ad": ["campaign_id", "adset_id", "ad_id"],
}
_LEAD_ACTION_TYPES = ("lead", "leadgen_grouped", "onsite_conversion.lead_grouped")
_PURCHASE_ACTION_TYPES = ("purchase", "omni_purchase",
                          "offsite_conversion.fb_pixel_purchase")


class MetaAdsApi(AdsApiBase):

    # ------------------------------------------------------------------ setup
    def _check_credentials(self):
        acc = self.account.sudo()
        if not acc.meta_access_token:
            raise AdsApiError("Meta account is missing its system-user access token.", "account")
        if not acc.external_account_id:
            raise AdsApiError("Meta account is missing the Ad Account ID.", "account")

    def _build_sdk(self):
        acc = self.account.sudo()
        session = FacebookSession(
            app_secret=acc.meta_app_secret or None,
            access_token=acc.meta_access_token,
        )
        return FacebookAdsApi(session, api_version=acc.meta_api_version or DEFAULT_API_VERSION)

    @property
    def account_obj(self):
        return AdAccount(f"act_{self.account.external_account_id}", api=self.sdk)

    # ------------------------------------------------------------------ errors
    def _map_exception(self, exc):
        if isinstance(exc, FacebookRequestError):
            code = exc.api_error_code()
            subcode = exc.api_error_subcode()
            status = exc.http_status()
            message = exc.api_error_message() or str(exc)
            failure = "unrecoverable"
            if code in _AUTH_CODES:
                failure = "auth"
            elif code in _PERMISSION_CODES or (code and 200 <= code <= 299):
                failure = "permission"
            elif code in _RATE_CODES:
                failure = "rate_limit"
            elif (code, subcode) == _TOO_MUCH_DATA:
                failure = "too_much_data"
            elif exc.api_transient_error() or (status or 0) >= 500 or code in (1, 2):
                failure = "recoverable"
            retry_after = self._usage_cooldown(self._headers_of(exc)) if failure == "rate_limit" else None
            return AdsApiError(message, failure, code=code, subcode=subcode,
                              retry_after=retry_after, http_status=status)
        if isinstance(exc, _requests_lib.exceptions.RequestException):
            return AdsApiError(f"Network error contacting Meta: {exc}", "network")
        return AdsApiError(str(exc), "unknown")

    def _capture_rate_state(self, result_or_exc):
        headers = self._headers_of(result_or_exc)
        if not headers:
            return None
        cooldown = self._usage_cooldown(headers)
        if cooldown:
            self.account.sudo().rate_limit_state = self._usage_snapshot(headers)
        return cooldown

    @staticmethod
    def _headers_of(obj):
        """Best-effort header extraction from SDK responses/cursors/errors."""
        for accessor in ("http_headers", "headers"):
            attr = getattr(obj, accessor, None)
            if attr is None:
                continue
            try:
                headers = attr() if callable(attr) else attr
            except Exception:  # noqa: BLE001 - purely observational
                continue
            if isinstance(headers, dict):
                return headers
        return None

    @classmethod
    def _usage_snapshot(cls, headers):
        snapshot = {}
        for name in ("x-app-usage", "x-business-use-case-usage", "x-fb-ads-insights-throttle"):
            raw = headers.get(name) or headers.get(name.title()) or headers.get(name.upper())
            if raw:
                try:
                    snapshot[name] = json.loads(raw)
                except (ValueError, TypeError):
                    snapshot[name] = raw
        return snapshot

    @classmethod
    def _usage_cooldown(cls, headers):
        """Return cooldown seconds when any usage metric crosses the soft limit."""
        if not headers:
            return None
        snapshot = cls._usage_snapshot(headers)
        cooldown = None
        app = snapshot.get("x-app-usage") or {}
        if isinstance(app, dict) and any(
                (app.get(k) or 0) >= _USAGE_SOFT_LIMIT
                for k in ("call_count", "total_time", "total_cputime")):
            cooldown = _SOFT_COOLDOWN
        buc = snapshot.get("x-business-use-case-usage") or {}
        if isinstance(buc, dict):
            for entries in buc.values():
                for entry in entries if isinstance(entries, list) else []:
                    eta = entry.get("estimated_time_to_regain_access") or 0
                    if eta:
                        cooldown = max(cooldown or 0, int(eta) * 60)
                    elif any((entry.get(k) or 0) >= _USAGE_SOFT_LIMIT
                             for k in ("call_count", "total_time", "total_cputime")):
                        cooldown = max(cooldown or 0, _SOFT_COOLDOWN)
        return cooldown

    # ------------------------------------------------------------------- reads
    def get_account_info(self):
        fields = ["name", "account_status", "currency", "timezone_name"]
        data = self._execute(
            lambda: self.account_obj.api_get(fields=fields).export_all_data(),
            op="get_account_info")
        return {
            "name": data.get("name"),
            "status": data.get("account_status"),
            "currency": data.get("currency"),
            "timezone": data.get("timezone_name"),
            "raw": data,
        }

    def _iter_edge(self, edge_callable, fields, params=None, max_pages=None, op="edge"):
        """Page-bounded iteration over an SDK Cursor; yields lists of dicts,
        one list per transport page (PAGE_SIZE items).

        The Cursor issues follow-up HTTP calls DURING iteration — outside
        `_execute` — so iteration errors are mapped here to keep the
        AdsApiError contract airtight."""
        params = dict(params or {}, limit=PAGE_SIZE)
        cursor = self._execute(lambda: edge_callable(fields=fields, params=params), op=op)
        page, pages = [], 0
        iterator = iter(cursor)
        while True:
            try:
                obj = next(iterator)
            except StopIteration:
                break
            except AdsApiError:
                raise
            except Exception as exc:  # noqa: BLE001 - mapped, never swallowed
                raise self._map_exception(exc) from exc
            page.append(obj.export_all_data() if hasattr(obj, "export_all_data") else dict(obj))
            if len(page) >= PAGE_SIZE:
                yield page
                page, pages = [], pages + 1
                if max_pages and pages >= max_pages:
                    return
        if page:
            yield page

    # -------------------------------------------------------- structure pull
    def list_campaigns(self, since=None, max_pages=None):
        """Yield pages of normalized campaign dicts.

        `since` is accepted for interface parity but a full pull is performed:
        the upsert is idempotent, and Graph edge filtering on updated_time is a
        pure optimisation with a silent-miss failure mode we refuse to risk.
        """
        for page in self._iter_edge(self.account_obj.get_campaigns,
                                    META_CAMPAIGN_FIELDS, max_pages=max_pages,
                                    op="list_campaigns"):
            yield [self._normalize_campaign(data) for data in page]

    def list_adsets(self, since=None, max_pages=None):
        for page in self._iter_edge(self.account_obj.get_ad_sets,
                                    META_ADSET_FIELDS, max_pages=max_pages,
                                    op="list_adsets"):
            yield [self._normalize_adset(data) for data in page]

    def list_ads(self, since=None, max_pages=None):
        for page in self._iter_edge(self.account_obj.get_ads,
                                    META_AD_FIELDS, max_pages=max_pages,
                                    op="list_ads"):
            yield [self._normalize_ad(data) for data in page]

    # -------------------------------------------------------- normalization
    def _budget_fields(self, data):
        daily = self._from_minor(data.get("daily_budget"), self.decimals)
        lifetime = self._from_minor(data.get("lifetime_budget"), self.decimals)
        if daily:
            return "daily", daily
        if lifetime:
            return "lifetime", lifetime
        return "none", 0.0

    def _normalize_campaign(self, data):
        budget_type, budget_amount = self._budget_fields(data)
        objective_raw = data.get("objective") or ""
        return {
            "external_id": str(data["id"]),
            "name": data.get("name") or f"Campaign {data['id']}",
            "status": _STATUS_MAP.get(data.get("status"), "paused"),
            "platform_status": data.get("effective_status") or data.get("status"),
            "objective": _OBJECTIVE_MAP.get(objective_raw, "other"),
            "objective_raw": objective_raw,
            "budget_type": budget_type,
            "budget_amount": budget_amount,
            "bid_strategy": data.get("bid_strategy"),
            "special_ad_categories": data.get("special_ad_categories") or [],
            "start_datetime": self._parse_dt(data.get("start_time")),
            "stop_datetime": self._parse_dt(data.get("stop_time")),
            "remote_updated_at": self._parse_dt(data.get("updated_time")),
            "provider_data": {"buying_type": data.get("buying_type")},
            "raw": data,
        }

    def _normalize_adset(self, data):
        budget_type, budget_amount = self._budget_fields(data)
        return {
            "external_id": str(data["id"]),
            "campaign_external_id": str(data.get("campaign_id") or ""),
            "name": data.get("name") or f"Ad Set {data['id']}",
            "status": _STATUS_MAP.get(data.get("status"), "paused"),
            "platform_status": data.get("effective_status") or data.get("status"),
            "budget_type": budget_type,
            "budget_amount": budget_amount,
            "optimization_goal": data.get("optimization_goal"),
            "billing_event": data.get("billing_event"),
            "targeting": data.get("targeting"),
            "promoted_object": data.get("promoted_object"),
            "start_datetime": self._parse_dt(data.get("start_time")),
            "stop_datetime": self._parse_dt(data.get("end_time")),
            "remote_updated_at": self._parse_dt(data.get("updated_time")),
            "provider_data": {},
            "raw": data,
        }

    # --------------------------------------------------------------- insights
    def _insight_request(self, level, since, until):
        fields = _INSIGHT_BASE_FIELDS + _INSIGHT_LEVEL_IDS[level]
        params = {
            "level": level,
            "time_increment": 1,
            "time_range": {"since": since.isoformat(), "until": until.isoformat()},
        }
        return fields, params

    def iter_insights(self, level, since, until, max_pages=None):
        """Yield pages of normalized daily insight rows.

        On Meta's 'too much data' error (code 100 / subcode 1487534) the date
        range is split in half recursively; a single day that still trips it
        re-raises so the caller can escalate to an async report job. Pages
        already yielded before a split simply get re-upserted — idempotent."""
        try:
            fields, params = self._insight_request(level, since, until)
            for page in self._iter_edge(self.account_obj.get_insights, fields,
                                        params=params, max_pages=max_pages,
                                        op=f"insights:{level}"):
                yield [self._normalize_insight(data) for data in page]
        except AdsApiError as exc:
            if exc.failure_type != "too_much_data" or since >= until:
                raise
            mid = since + (until - since) / 2
            yield from self.iter_insights(level, since, mid, max_pages=max_pages)
            yield from self.iter_insights(level, mid + timedelta(days=1), until,
                                          max_pages=max_pages)

    def start_insights_job(self, level, since, until):
        fields, params = self._insight_request(level, since, until)
        job = self._execute(
            lambda: self.account_obj.get_insights(fields=fields, params=params,
                                                  is_async=True),
            op=f"insights_job:{level}")
        return str(job[AdReportRun.Field.id] if AdReportRun.Field.id in job
                   else job["report_run_id"])

    def poll_insights_job(self, report_run_id):
        run = self._execute(
            lambda: AdReportRun(report_run_id, api=self.sdk).api_get(),
            op="insights_job_poll")
        data = run.export_all_data() if hasattr(run, "export_all_data") else dict(run)
        return {
            "status": data.get("async_status"),
            "percent": data.get("async_percent_completion"),
        }

    def iter_insights_results(self, report_run_id, max_pages=None):
        run = AdReportRun(report_run_id, api=self.sdk)
        for page in self._iter_edge(
                lambda fields=None, params=None: run.get_result(params=params),
                fields=None, max_pages=max_pages, op="insights_job_result"):
            yield [self._normalize_insight(data) for data in page]

    # ------------------------------------------------------------------ create
    def create_object(self, object_type, record, values):
        """Create a campaign/adset/ad on Meta. Everything lands PAUSED — going
        live is always an explicit separate action. Returns {'external_id'}."""
        handler = {"campaign": self._create_campaign,
                   "adset": self._create_adset,
                   "ad": self._create_ad,
                   "audience": self._create_audience}.get(object_type)
        if not handler:
            raise AdsApiError(f"Meta cannot create object type {object_type}",
                              "validation")
        return handler(record, values)

    def _create_campaign(self, record, values):
        params = {
            "name": values.get("title") or values.get("name"),
            "objective": values["objective_raw"],
            "status": "PAUSED",
            "special_ad_categories": values.get("special_ad_categories") or [],
        }
        if values.get("budget_type") == "daily" and values.get("budget_amount"):
            params["daily_budget"] = self._to_minor(values["budget_amount"],
                                                    self.decimals)
        elif values.get("budget_type") == "lifetime" and values.get("budget_amount"):
            params["lifetime_budget"] = self._to_minor(values["budget_amount"],
                                                       self.decimals)
        if values.get("bid_strategy"):
            params["bid_strategy"] = values["bid_strategy"]
        result = self._execute(
            lambda: self.account_obj.create_campaign(params=params),
            op="create_campaign")
        return {"external_id": str(result["id"])}

    def _create_adset(self, record, values):
        params = {
            "name": values["name"],
            "campaign_id": values["campaign_external_id"],
            "optimization_goal": values["optimization_goal"],
            "billing_event": values["billing_event"],
            "targeting": values["targeting"],
            "status": "PAUSED",
        }
        if values.get("budget_type") == "daily" and values.get("budget_amount"):
            params["daily_budget"] = self._to_minor(values["budget_amount"],
                                                    self.decimals)
        elif values.get("budget_type") == "lifetime" and values.get("budget_amount"):
            params["lifetime_budget"] = self._to_minor(values["budget_amount"],
                                                       self.decimals)
        if values.get("start_datetime"):
            params["start_time"] = self._meta_datetime(values["start_datetime"])
        if values.get("stop_datetime"):
            params["end_time"] = self._meta_datetime(values["stop_datetime"])
        result = self._execute(
            lambda: self.account_obj.create_ad_set(params=params),
            op="create_adset")
        return {"external_id": str(result["id"])}

    def _create_ad(self, record, values):
        creative_id = values.get("creative_external_id")
        if not creative_id and values.get("creative"):
            creative = self._execute(
                lambda: self.account_obj.create_ad_creative(params={
                    "name": values["creative"].get("name") or values["name"],
                    "object_story_spec": values["creative"]["object_story_spec"],
                }),
                op="create_creative")
            creative_id = str(creative["id"])
        if not creative_id:
            raise AdsApiError("An ad needs a creative (existing creative or an "
                              "object_story_spec).", "validation")
        result = self._execute(
            lambda: self.account_obj.create_ad(params={
                "name": values["name"],
                "adset_id": values["adset_external_id"],
                "creative": {"creative_id": creative_id},
                "status": "PAUSED",
            }),
            op="create_ad")
        return {"external_id": str(result["id"]),
                "creative_external_id": creative_id}

    # --------------------------------------------------------------- audiences
    _AUDIENCE_TYPE = {"CUSTOM": "custom", "LOOKALIKE": "lookalike",
                      "WEBSITE": "website", "ENGAGEMENT": "website",
                      "CLAIM": "saved", "PARTNER": "custom"}
    META_AUDIENCE_FIELDS = ["id", "name", "description", "subtype",
                            "approximate_count_lower_bound", "delivery_status",
                            "retention_days"]

    def list_audiences(self, since=None, max_pages=None):
        for page in self._iter_edge(self.account_obj.get_custom_audiences,
                                    self.META_AUDIENCE_FIELDS, max_pages=max_pages,
                                    op="list_audiences"):
            yield [self._normalize_audience(data) for data in page]

    def _normalize_audience(self, data):
        delivery = data.get("delivery_status") or {}
        return {
            "external_id": str(data["id"]),
            "name": data.get("name") or f"Audience {data['id']}",
            "description": data.get("description"),
            "audience_type": self._AUDIENCE_TYPE.get(data.get("subtype"), "custom"),
            "approximate_count": int(data.get("approximate_count_lower_bound") or 0),
            "platform_status": str(delivery.get("description") or delivery.get("code")
                                   or ""),
            "retention_days": int(data.get("retention_days") or 0),
            "remote_updated_at": None,
            "provider_data": {},
            "raw": data,
        }

    def _create_audience(self, record, values):
        result = self._execute(
            lambda: self.account_obj.create_custom_audience(params={
                "name": values["name"],
                "description": values.get("description") or "",
                "subtype": "CUSTOM",
                "customer_file_source": "USER_PROVIDED_ONLY",
            }),
            op="create_audience")
        return {"external_id": str(result["id"])}

    def upload_audience_users(self, record, rows):
        """`rows` = [{'email': sha256|None, 'phone': sha256|None}], already
        hashed. Emails and phones upload as separate single-column batches."""
        audience = CustomAudience(record.external_id, api=self.sdk)
        for schema, key in (("EMAIL", "email"), ("PHONE", "phone")):
            data = [[row[key]] for row in rows if row.get(key)]
            if not data:
                continue
            self._execute(
                lambda schema=schema, data=data: audience.add_users(
                    schema=[schema], users=data, is_raw=True, pre_hashed=True),
                op=f"audience_users:{schema.lower()}")

    # ------------------------------------------------------------------- leads
    #: Meta lead pulls are addressed per form (per-Page rate budget).
    LEADS_PER_FORM = True

    META_LEAD_FIELDS = ["id", "created_time", "ad_id", "adset_id", "campaign_id",
                        "form_id", "field_data", "is_organic"]
    META_FORM_FIELDS = ["id", "name", "status", "locale", "questions", "page_id"]

    @property
    def _lead_api(self):
        """Lead endpoints prefer the Page token (per-Page BUC budget); the
        system-user token is the fallback."""
        acc = self.account.sudo()
        if not acc.meta_page_access_token:
            return self.sdk
        session = FacebookSession(app_secret=acc.meta_app_secret or None,
                                  access_token=acc.meta_page_access_token)
        return FacebookAdsApi(session,
                              api_version=acc.meta_api_version or DEFAULT_API_VERSION)

    def list_lead_forms(self):
        acc = self.account.sudo()
        if not acc.meta_page_id:
            raise AdsApiError("Meta account is missing the Page ID (lead forms "
                              "belong to the Page).", "account")
        page = Page(acc.meta_page_id, api=self._lead_api)
        rows = []
        for chunk in self._iter_edge(page.get_lead_gen_forms, self.META_FORM_FIELDS,
                                     op="list_lead_forms"):
            rows.extend(self._normalize_lead_form(data) for data in chunk)
        return rows

    def iter_leads(self, form_external_id=None, since=None, max_pages=None):
        params = {}
        if since:
            params["filtering"] = [{
                "field": "time_created",
                "operator": "GREATER_THAN",
                "value": int(since.timestamp()) if hasattr(since, "timestamp")
                else int(since),
            }]
        form = LeadgenForm(form_external_id, api=self._lead_api)
        for page in self._iter_edge(form.get_leads, self.META_LEAD_FIELDS,
                                    params=params, max_pages=max_pages,
                                    op="iter_leads"):
            yield [self._normalize_lead(data) for data in page]

    def get_lead(self, lead_external_id):
        data = self._execute(
            lambda: Lead(lead_external_id, api=self._lead_api).api_get(
                fields=self.META_LEAD_FIELDS).export_all_data(),
            op="get_lead")
        return self._normalize_lead(data)

    # ------------------------------------------------------------------ mutate
    _STATUS_PUSH = {"enabled": "ACTIVE", "paused": "PAUSED",
                    "archived": "ARCHIVED", "removed": "DELETED"}
    _MUTABLE = {"campaign": Campaign, "adset": AdSet, "ad": Ad}

    def _mutable(self, object_type, record):
        cls = self._MUTABLE.get(object_type)
        if not cls:
            raise AdsApiError(f"Meta cannot mutate object type {object_type}",
                              "validation")
        return cls(record.external_id, api=self.sdk)

    @staticmethod
    def _meta_datetime(value):
        # _json_safe stores naive-UTC Odoo strings; Graph wants ISO8601 + tz.
        return f"{str(value)[:19].replace(' ', 'T')}+0000"

    def set_status(self, object_type, record, status):
        obj = self._mutable(object_type, record)
        self._execute(
            lambda: obj.api_update(params={"status": self._STATUS_PUSH[status]}),
            op=f"set_status:{object_type}")

    def set_budget(self, object_type, record, budget_type, amount):
        if object_type not in ("campaign", "adset"):
            raise AdsApiError("Meta budgets live on campaigns or ad sets.",
                              "validation")
        field = "daily_budget" if budget_type == "daily" else "lifetime_budget"
        obj = self._mutable(object_type, record)
        self._execute(
            lambda: obj.api_update(params={field: self._to_minor(amount,
                                                                 self.decimals)}),
            op=f"set_budget:{object_type}")

    def update_object(self, object_type, record, values, changed):
        params = self._push_params(object_type, values, changed)
        if not params:
            return
        obj = self._mutable(object_type, record)
        self._execute(lambda: obj.api_update(params=params),
                      op=f"update:{object_type}")

    def _push_params(self, object_type, values, changed):
        params = {}
        stop_key = "stop_time" if object_type == "campaign" else "end_time"
        for key in changed:
            value = values.get(key)
            if value in (None, "", False):
                continue
            if key in ("title", "name"):
                params["name"] = value
            elif key == "status":
                params["status"] = self._STATUS_PUSH[value]
            elif key == "bid_strategy":
                params["bid_strategy"] = value
            elif key == "optimization_goal":
                params["optimization_goal"] = value
            elif key == "start_datetime":
                params["start_time"] = self._meta_datetime(value)
            elif key == "stop_datetime":
                params[stop_key] = self._meta_datetime(value)
            # budget_* keys never reach here (routed to set_budget);
            # cpc_bid is a Google-only field.
        return params

    @staticmethod
    def _normalize_lead_form(data):
        return {
            "external_id": str(data["id"]),
            "name": data.get("name"),
            "status": data.get("status"),
            "locale": data.get("locale"),
            "questions": data.get("questions"),
            "page_external_id": str(data.get("page_id") or ""),
        }

    def _normalize_lead(self, data):
        return {
            "external_id": str(data["id"]),
            "form_external_id": str(data.get("form_id") or "") or None,
            "created_time": self._parse_dt(data.get("created_time")),
            "campaign_external_id": str(data.get("campaign_id") or "") or None,
            "adset_external_id": str(data.get("adset_id") or "") or None,
            "ad_external_id": str(data.get("ad_id") or "") or None,
            "is_organic": bool(data.get("is_organic")),
            "gclid": None,
            "fields": [{"key": (entry.get("name") or "").lower(),
                        "values": entry.get("values") or []}
                       for entry in data.get("field_data") or []],
            "raw": data,
        }

    @staticmethod
    def _sum_actions(entries, action_types):
        total = 0.0
        for entry in entries or []:
            if entry.get("action_type") in action_types:
                try:
                    total += float(entry.get("value") or 0)
                except (TypeError, ValueError):
                    continue
        return total

    def _normalize_insight(self, data):
        actions = data.get("actions")
        action_values = data.get("action_values")
        return {
            "date": self._parse_date(data.get("date_start")),
            "campaign_external_id": str(data.get("campaign_id") or "") or None,
            "adset_external_id": str(data.get("adset_id") or "") or None,
            "ad_external_id": str(data.get("ad_id") or "") or None,
            "spend": float(data.get("spend") or 0),
            "impressions": int(data.get("impressions") or 0),
            "clicks": int(data.get("clicks") or 0),
            "reach": int(data.get("reach") or 0),
            "conversions": self._sum_actions(actions, _PURCHASE_ACTION_TYPES),
            "conversion_value": self._sum_actions(action_values, _PURCHASE_ACTION_TYPES),
            "video_views": int(self._sum_actions(actions, ("video_view",))),
            "leads_count": int(self._sum_actions(actions, _LEAD_ACTION_TYPES)),
        }

    def _normalize_ad(self, data):
        creative = data.get("creative") or {}
        return {
            "external_id": str(data["id"]),
            "adset_external_id": str(data.get("adset_id") or ""),
            "name": data.get("name") or f"Ad {data['id']}",
            "status": _STATUS_MAP.get(data.get("status"), "paused"),
            "platform_status": data.get("effective_status") or data.get("status"),
            "preview_url": data.get("preview_shareable_link"),
            "final_urls": [],
            "creative": {
                "external_id": str(creative["id"]) if creative.get("id") else None,
                "name": creative.get("name"),
                "title": creative.get("title"),
                "body": creative.get("body"),
                "call_to_action": creative.get("call_to_action_type"),
                "image_url": creative.get("image_url"),
                "thumbnail_url": creative.get("thumbnail_url"),
                "object_story_spec": creative.get("object_story_spec"),
                "raw": creative,
            } if creative else None,
            "remote_updated_at": self._parse_dt(data.get("updated_time")),
            "provider_data": {},
            "raw": data,
        }
