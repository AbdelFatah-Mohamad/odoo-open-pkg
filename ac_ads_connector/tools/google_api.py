# -*- coding: utf-8 -*-
"""Google Ads API client over the official ``google-ads`` SDK.

Auth is one of two account-configured modes:

  * ``oauth_refresh`` — developer token + OAuth client id/secret + refresh
    token, fed to ``GoogleAdsClient.load_from_dict``. The refresh token is
    obtained once through the "Connect Google" wizard (see
    ``wizard/google_auth_wizard.py`` + ``/ads/google/oauth_callback``).
  * ``service_account`` — a plain service-account JSON key whose email was
    added as a **user of the Google Ads account** in the Ads UI. No
    domain-wide delegation / impersonation involved.

The API version is pinned per account (``google_api_version``, default v25).
Money moves in micros; GAQL strings are built only through the private
``_q_*`` builders with ``_gaql_quote`` — never interpolate caller input.
Google signals quota only in error payloads (``QuotaError`` +
``retry_delay``), which we persist as an account cooldown.
"""
import json
import logging
import re

from .ads_api import AdsApiBase, AdsApiError

_logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v25"
ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"

_RECOVERABLE_STATUSES = {"INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED", "ABORTED"}

_STATUS_MAP = {"ENABLED": "enabled", "PAUSED": "paused", "REMOVED": "removed"}

#: advertising_channel_type -> the module's unified objective vocabulary.
_CHANNEL_OBJECTIVE = {
    "SEARCH": "traffic",
    "DISPLAY": "awareness",
    "VIDEO": "awareness",
    "SHOPPING": "sales",
    "PERFORMANCE_MAX": "sales",
    "DEMAND_GEN": "engagement",
    "DISCOVERY": "engagement",
    "LOCAL": "sales",
    "SMART": "other",
    "MULTI_CHANNEL": "app",
    "TRAVEL": "sales",
}


def _enum_name(value):
    return getattr(value, "name", str(value) if value not in (None, "") else "")


def _tail(resource_name):
    return str(resource_name).rsplit("/", 1)[-1] if resource_name else ""


def _lazy_imports():
    """google-ads drags in grpc + protos; import once, on first client build."""
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
    from google.auth.exceptions import GoogleAuthError
    from google.oauth2 import service_account as sa_module
    return GoogleAdsClient, GoogleAdsException, GoogleAuthError, sa_module


class GoogleAdsApi(AdsApiBase):

    # ------------------------------------------------------------------ setup
    def _check_credentials(self):
        acc = self.account.sudo()
        if not acc.google_developer_token:
            raise AdsApiError("Google account is missing the developer token.", "account")
        if not acc.external_account_id:
            raise AdsApiError("Google account is missing the Customer ID.", "account")
        if acc.google_auth_mode == "service_account":
            if not acc.google_sa_json:
                raise AdsApiError("Google account is missing the service-account JSON key.", "account")
        else:
            if not (acc.google_client_id and acc.google_client_secret and acc.google_refresh_token):
                raise AdsApiError(
                    "Google account is missing OAuth credentials (client id/secret + refresh "
                    "token — run the Connect Google wizard).", "account")

    @property
    def customer_id(self):
        return re.sub(r"\D", "", self.account.external_account_id or "")

    def _build_sdk(self):
        GoogleAdsClient, _exc, _auth_exc, sa_module = _lazy_imports()
        acc = self.account.sudo()
        version = acc.google_api_version or DEFAULT_API_VERSION
        login_cid = re.sub(r"\D", "", acc.google_login_customer_id or "") or None
        if acc.google_auth_mode == "service_account":
            try:
                info = json.loads(acc.google_sa_json)
            except ValueError as exc:
                raise AdsApiError(f"Service-account key is not valid JSON: {exc}", "account")
            credentials = sa_module.Credentials.from_service_account_info(
                info, scopes=[ADWORDS_SCOPE])
            return GoogleAdsClient(
                credentials=credentials,
                developer_token=acc.google_developer_token,
                login_customer_id=login_cid,
                use_proto_plus=True,
                version=version,
            )
        config = {
            "developer_token": acc.google_developer_token,
            "client_id": acc.google_client_id,
            "client_secret": acc.google_client_secret,
            "refresh_token": acc.google_refresh_token,
            "use_proto_plus": True,
        }
        if login_cid:
            config["login_customer_id"] = login_cid
        return GoogleAdsClient.load_from_dict(config, version=version)

    # ------------------------------------------------------------------ errors
    def _map_exception(self, exc):
        _client, GoogleAdsException, GoogleAuthError, _sa = _lazy_imports()
        if isinstance(exc, GoogleAdsException):
            return self._map_ads_exception(exc)
        if isinstance(exc, GoogleAuthError):
            return AdsApiError(f"Google authentication failed: {exc}", "auth")
        # grpc transport errors surface as RpcError subclasses without the
        # GoogleAdsException wrapper when the failure precedes the API layer.
        name = type(exc).__name__
        if "Rpc" in name or "Connection" in name or "Timeout" in name:
            return AdsApiError(f"Network error contacting Google Ads: {exc}", "network")
        return AdsApiError(str(exc), "unknown")

    def _map_ads_exception(self, exc):
        status = getattr(getattr(exc, "error", None), "code", lambda: None)()
        status_name = getattr(status, "name", str(status) if status else "")
        message_parts, failure, retry_after = [], None, None
        for error in getattr(getattr(exc, "failure", None), "errors", []) or []:
            message_parts.append(error.message)
            code_kind = error.error_code.WhichOneof("error_code") if hasattr(
                error.error_code, "WhichOneof") else None
            if code_kind == "quota_error":
                failure = "rate_limit"
                delay = getattr(getattr(error, "details", None), "quota_error_details", None)
                delay = getattr(delay, "retry_delay", None)
                seconds = getattr(delay, "seconds", 0) if delay else 0
                if seconds:
                    retry_after = int(seconds)
            elif code_kind in ("authentication_error", "authorization_error") and not failure:
                failure = "auth" if code_kind == "authentication_error" else "permission"
            elif code_kind in ("field_error", "mutate_error", "operation_access_denied_error",
                               "request_error", "range_error", "string_length_error",
                               "campaign_error", "ad_group_error", "ad_error") and not failure:
                failure = "validation"
        if not failure:
            failure = "recoverable" if status_name in _RECOVERABLE_STATUSES else "unrecoverable"
        message = "; ".join(message_parts) or str(exc)
        mapped = AdsApiError(message, failure, code=status_name, retry_after=retry_after)
        return mapped

    def _capture_rate_state(self, result_or_exc):
        # Google has no usage headers on success; quota errors carry
        # retry_delay which _map_exception already exposes — persist it here.
        mapped = getattr(result_or_exc, "_ads_mapped", None)
        if isinstance(result_or_exc, Exception):
            probe = self._map_exception(result_or_exc) if mapped is None else mapped
            if probe.failure_type == "rate_limit" and probe.retry_after:
                return probe.retry_after
        return None

    # -------------------------------------------------------------------- gaql
    @staticmethod
    def _gaql_quote(value):
        """Quote a literal for GAQL. Only str/int/date-ish values are accepted."""
        text = str(value)
        if not re.fullmatch(r"[\w\-\.: ]*", text):
            raise AdsApiError(f"Refusing to inline suspicious GAQL literal: {text!r}",
                              "validation")
        return f"'{text}'"

    def _search(self, query, max_pages=None, op="search"):
        """Yield lists of GoogleAdsRow, one list per page."""
        service = self.sdk.get_service("GoogleAdsService")
        request_cls = self.sdk.get_type("SearchGoogleAdsRequest")

        pages = 0
        page_token = None
        while True:
            request = request_cls(customer_id=self.customer_id, query=query)
            if page_token:
                request.page_token = page_token
            response = self._execute(service.search, request=request, op=op)
            rows = list(response)
            if rows:
                yield rows
            pages += 1
            page_token = getattr(response, "next_page_token", None)
            if not page_token or (max_pages and pages >= max_pages):
                return

    # ------------------------------------------------------------------- reads
    def get_account_info(self):
        query = ("SELECT customer.id, customer.descriptive_name, "
                 "customer.currency_code, customer.time_zone FROM customer")
        for rows in self._search(query, max_pages=1, op="get_account_info"):
            customer = rows[0].customer
            if str(customer.id) != self.customer_id:
                raise AdsApiError(
                    f"Credentials answer for customer {customer.id}, but the account is "
                    f"configured for {self.customer_id}.", "account")
            return {
                "name": customer.descriptive_name,
                "status": "active",
                "currency": customer.currency_code,
                "timezone": customer.time_zone,
                "raw": {"id": str(customer.id)},
            }
        raise AdsApiError("Google returned no customer row for this account.", "unrecoverable")

    def list_accessible_customers(self):
        service = self.sdk.get_service("CustomerService")
        response = self._execute(service.list_accessible_customers,
                                 op="list_accessible_customers")
        return [name.rsplit("/", 1)[-1] for name in response.resource_names]

    # -------------------------------------------------------- structure pull
    def _q_campaigns(self):
        return ("SELECT campaign.id, campaign.name, campaign.status, "
                "campaign.advertising_channel_type, campaign.start_date, "
                "campaign.end_date, campaign.bidding_strategy_type, "
                "campaign.campaign_budget, campaign.resource_name, "
                "campaign_budget.amount_micros, campaign_budget.total_amount_micros "
                "FROM campaign WHERE campaign.status != 'REMOVED'")

    def _q_ad_groups(self):
        return ("SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.type, "
                "ad_group.campaign, ad_group.cpc_bid_micros, ad_group.resource_name "
                "FROM ad_group WHERE ad_group.status != 'REMOVED'")

    def _q_ads(self):
        return ("SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.status, "
                "ad_group_ad.ad.type, ad_group_ad.ad.final_urls, ad_group_ad.ad_group, "
                "ad_group_ad.resource_name "
                "FROM ad_group_ad WHERE ad_group_ad.status != 'REMOVED'")

    def list_campaigns(self, since=None, max_pages=None):
        """Yield pages of normalized campaign dicts. Google exposes no
        updated_time watermark on structure — every pull is a full bounded pull
        and `since` is accepted only for interface parity."""
        for rows in self._search(self._q_campaigns(), max_pages=max_pages,
                                 op="list_campaigns"):
            yield [self._normalize_campaign(row) for row in rows]

    def list_adsets(self, since=None, max_pages=None):
        for rows in self._search(self._q_ad_groups(), max_pages=max_pages,
                                 op="list_adsets"):
            yield [self._normalize_ad_group(row) for row in rows]

    def list_ads(self, since=None, max_pages=None):
        for rows in self._search(self._q_ads(), max_pages=max_pages, op="list_ads"):
            yield [self._normalize_ad(row) for row in rows]

    # ---------------------------------------------------------------- insights
    _INSIGHT_RESOURCES = {
        "account": ("customer", ""),
        "campaign": ("campaign", "campaign.id"),
        "adset": ("ad_group", "campaign.id, ad_group.id"),
        "ad": ("ad_group_ad", "campaign.id, ad_group.id, ad_group_ad.ad.id"),
    }
    _INSIGHT_METRICS = ("metrics.cost_micros, metrics.impressions, metrics.clicks, "
                        "metrics.conversions, metrics.conversions_value, "
                        "metrics.video_views")

    def _q_insights(self, level, since, until):
        resource, id_fields = self._INSIGHT_RESOURCES[level]
        select = ", ".join(filter(None, [id_fields, "segments.date",
                                         self._INSIGHT_METRICS]))
        return (f"SELECT {select} FROM {resource} "
                f"WHERE segments.date BETWEEN {self._gaql_quote(since.isoformat())} "
                f"AND {self._gaql_quote(until.isoformat())}")

    def iter_insights(self, level, since, until, max_pages=None):
        """Yield pages of normalized daily insight rows. Google has no
        'too much data' failure mode — paging absorbs any volume.
        `leads_count` stays 0 here: Google lead volume arrives through the
        lead-form sync, not the metrics report."""
        query = self._q_insights(level, since, until)
        for rows in self._search(query, max_pages=max_pages,
                                 op=f"insights:{level}"):
            yield [self._normalize_insight_row(level, row) for row in rows]

    def _normalize_insight_row(self, level, row):
        metrics = row.metrics
        campaign = getattr(getattr(row, "campaign", None), "id", None)
        ad_group = getattr(getattr(row, "ad_group", None), "id", None)
        ad = getattr(getattr(getattr(row, "ad_group_ad", None), "ad", None), "id", None)
        return {
            "date": self._parse_date(row.segments.date),
            "campaign_external_id": str(campaign) if campaign else None,
            "adset_external_id": str(ad_group) if ad_group else None,
            "ad_external_id": str(ad) if ad else None,
            "spend": self._from_micros(metrics.cost_micros or 0),
            "impressions": int(metrics.impressions or 0),
            "clicks": int(metrics.clicks or 0),
            "reach": 0,
            "conversions": float(metrics.conversions or 0),
            "conversion_value": float(metrics.conversions_value or 0),
            "video_views": int(metrics.video_views or 0),
            "leads_count": 0,
        }

    # ------------------------------------------------------------------ mutate
    _STATUS_PUSH = {"enabled": "ENABLED", "paused": "PAUSED",
                    "removed": "REMOVED", "archived": "REMOVED"}
    _MUTATE_SERVICES = {
        "campaign": ("CampaignService", "CampaignOperation", "mutate_campaigns",
                     "CampaignStatusEnum", "campaigns"),
        "adset": ("AdGroupService", "AdGroupOperation", "mutate_ad_groups",
                  "AdGroupStatusEnum", "adGroups"),
        "ad": ("AdGroupAdService", "AdGroupAdOperation", "mutate_ad_group_ads",
               "AdGroupAdStatusEnum", "adGroupAds"),
    }

    def _resource_name(self, object_type, record):
        resource = (record.provider_data or {}).get("resource_name")
        if resource:
            return resource
        path = self._MUTATE_SERVICES[object_type][4]
        return f"customers/{self.customer_id}/{path}/{record.external_id}"

    def _status_enum(self, enum_name, status_name):
        return getattr(getattr(self.sdk.enums, enum_name), status_name)

    def _mutate_update(self, object_type, resource_name, setter, mask_paths, op):
        service_name, op_type, method, _enum, _path = self._MUTATE_SERVICES[object_type]
        service = self.sdk.get_service(service_name)
        operation = self.sdk.get_type(op_type)()
        target = operation.update
        target.resource_name = resource_name
        setter(target)
        operation.update_mask.paths.extend(mask_paths)
        self._execute(getattr(service, method), customer_id=self.customer_id,
                      operations=[operation], op=op)

    def set_status(self, object_type, record, status):
        enum_name = self._MUTATE_SERVICES[object_type][3]
        status_value = self._status_enum(enum_name, self._STATUS_PUSH[status])

        def setter(target):
            target.status = status_value

        self._mutate_update(object_type, self._resource_name(object_type, record),
                            setter, ["status"], op=f"set_status:{object_type}")

    def set_budget(self, object_type, record, budget_type, amount):
        if object_type != "campaign":
            raise AdsApiError("Google budgets are campaign-level resources.",
                              "validation")
        budget_resource = record.google_budget_resource
        if not budget_resource:
            raise AdsApiError("Campaign has no synced budget resource yet — "
                              "run a structure sync first.", "validation")
        service = self.sdk.get_service("CampaignBudgetService")
        operation = self.sdk.get_type("CampaignBudgetOperation")()
        field = ("total_amount_micros" if budget_type == "lifetime"
                 else "amount_micros")
        operation.update.resource_name = budget_resource
        setattr(operation.update, field, self._to_micros(amount))
        operation.update_mask.paths.append(field)
        self._execute(service.mutate_campaign_budgets, customer_id=self.customer_id,
                      operations=[operation], op="set_budget")

    #: our field -> (google field mask path, converter kind)
    _PUSH_PATHS = {
        "campaign": {"title": ("name", "text"), "name": ("name", "text"),
                     "status": ("status", "status"),
                     "start_datetime": ("start_date", "date"),
                     "stop_datetime": ("end_date", "date")},
        "adset": {"name": ("name", "text"), "status": ("status", "status"),
                  "cpc_bid": ("cpc_bid_micros", "micros")},
        # Google ad CONTENT is immutable; only the wrapper status can change.
        "ad": {"status": ("status", "status")},
    }

    def update_object(self, object_type, record, values, changed):
        paths, setters = [], []
        enum_name = self._MUTATE_SERVICES[object_type][3]
        for key in changed:
            spec = self._PUSH_PATHS[object_type].get(key)
            value = values.get(key)
            if not spec or value in (None, "", False):
                continue
            path, kind = spec
            if kind == "text":
                converted = value
            elif kind == "status":
                converted = self._status_enum(enum_name, self._STATUS_PUSH[value])
            elif kind == "date":
                converted = str(value)[:10]
            elif kind == "micros":
                converted = self._to_micros(value)
            paths.append(path)
            setters.append((path, converted))
        if not paths:
            return

        def setter(target):
            for path, converted in setters:
                setattr(target, path, converted)

        self._mutate_update(object_type, self._resource_name(object_type, record),
                            setter, paths, op=f"update:{object_type}")

    # ------------------------------------------------------------------ create
    #: unified objective -> default advertising channel when objective_raw unset.
    _OBJECTIVE_CHANNEL = {"traffic": "SEARCH", "awareness": "DISPLAY",
                          "engagement": "DEMAND_GEN", "sales": "PERFORMANCE_MAX",
                          "leads": "SEARCH", "app": "MULTI_CHANNEL"}

    def create_object(self, object_type, record, values):
        """Create a campaign (atomic budget+campaign mutate with a temp -1
        resource id) or an ad group. Everything lands PAUSED. Google ad
        CONTENT creation is deliberately not supported — author ads in Google
        Ads and let the sync mirror them back."""
        if object_type == "campaign":
            return self._create_campaign(record, values)
        if object_type == "adset":
            return self._create_ad_group(record, values)
        if object_type == "audience":
            return self._create_user_list(record, values)
        raise AdsApiError(
            "Google ads must be authored in Google Ads itself (ad content is "
            "immutable through this connector); the sync will mirror them back.",
            "validation")

    def _create_campaign(self, record, values):
        name = values.get("title") or values.get("name")
        channel = values.get("objective_raw") or self._OBJECTIVE_CHANNEL.get(
            values.get("objective"), "SEARCH")
        MutateOperation = self.sdk.get_type("MutateOperation")
        temp_budget = f"customers/{self.customer_id}/campaignBudgets/-1"

        budget_op = MutateOperation()
        budget = budget_op.campaign_budget_operation.create
        budget.resource_name = temp_budget
        budget.name = f"{name} budget"
        budget.explicitly_shared = False
        if values.get("budget_type") == "lifetime":
            budget.total_amount_micros = self._to_micros(values.get("budget_amount"))
        else:
            budget.amount_micros = self._to_micros(values.get("budget_amount"))

        campaign_op = MutateOperation()
        campaign = campaign_op.campaign_operation.create
        campaign.name = name
        campaign.campaign_budget = temp_budget
        campaign.status = self._status_enum("CampaignStatusEnum", "PAUSED")
        campaign.advertising_channel_type = getattr(
            self.sdk.enums.AdvertisingChannelTypeEnum, channel)
        if values.get("start_datetime"):
            campaign.start_date = str(values["start_datetime"])[:10]
        if values.get("stop_datetime"):
            campaign.end_date = str(values["stop_datetime"])[:10]

        service = self.sdk.get_service("GoogleAdsService")
        response = self._execute(service.mutate, customer_id=self.customer_id,
                                 mutate_operations=[budget_op, campaign_op],
                                 op="create_campaign")
        results = list(response.mutate_operation_responses)
        budget_resource = results[0].campaign_budget_result.resource_name
        campaign_resource = results[1].campaign_result.resource_name
        return {"external_id": _tail(campaign_resource),
                "resource_name": campaign_resource,
                "budget_resource": budget_resource}

    def _create_ad_group(self, record, values):
        service = self.sdk.get_service("AdGroupService")
        operation = self.sdk.get_type("AdGroupOperation")()
        ad_group = operation.create
        ad_group.name = values["name"]
        ad_group.campaign = values["campaign_resource_name"]
        ad_group.status = self._status_enum("AdGroupStatusEnum", "PAUSED")
        if values.get("cpc_bid"):
            ad_group.cpc_bid_micros = self._to_micros(values["cpc_bid"])
        response = self._execute(service.mutate_ad_groups,
                                 customer_id=self.customer_id,
                                 operations=[operation], op="create_adset")
        resource = response.results[0].resource_name
        return {"external_id": _tail(resource), "resource_name": resource}

    # --------------------------------------------------------------- audiences
    def list_audiences(self, since=None, max_pages=None):
        query = ("SELECT user_list.id, user_list.name, user_list.description, "
                 "user_list.size_for_search, user_list.type, "
                 "user_list.resource_name FROM user_list")
        for rows in self._search(query, max_pages=max_pages, op="list_audiences"):
            yield [self._normalize_user_list(row) for row in rows]

    def _normalize_user_list(self, row):
        user_list = row.user_list
        return {
            "external_id": str(user_list.id),
            "name": user_list.name or f"User list {user_list.id}",
            "description": user_list.description or "",
            "audience_type": "user_list",
            "approximate_count": int(user_list.size_for_search or 0),
            "platform_status": _enum_name(user_list.type_),
            "retention_days": 0,
            "remote_updated_at": None,
            "provider_data": {"resource_name": user_list.resource_name},
            "raw": {"id": str(user_list.id)},
        }

    def _create_user_list(self, record, values):
        service = self.sdk.get_service("UserListService")
        operation = self.sdk.get_type("UserListOperation")()
        user_list = operation.create
        user_list.name = values["name"]
        user_list.description = values.get("description") or ""
        user_list.crm_based_user_list.upload_key_type = (
            self.sdk.enums.CustomerMatchUploadKeyTypeEnum.CONTACT_INFO)
        response = self._execute(service.mutate_user_lists,
                                 customer_id=self.customer_id,
                                 operations=[operation], op="create_audience")
        resource = response.results[0].resource_name
        return {"external_id": _tail(resource), "resource_name": resource}

    def upload_audience_users(self, record, rows):
        """Customer-match upload via an offline user data job:
        create -> add operations -> run."""
        user_list_resource = (record.provider_data or {}).get("resource_name") \
            or f"customers/{self.customer_id}/userLists/{record.external_id}"
        service = self.sdk.get_service("OfflineUserDataJobService")
        job = self.sdk.get_type("OfflineUserDataJob")()
        job.type_ = self.sdk.enums.OfflineUserDataJobTypeEnum.CUSTOMER_MATCH_USER_LIST
        job.customer_match_user_list_metadata.user_list = user_list_resource
        created = self._execute(service.create_offline_user_data_job,
                                customer_id=self.customer_id, job=job,
                                op="audience_job_create")
        job_resource = created.resource_name
        operations = []
        for row in rows:
            operation = self.sdk.get_type("OfflineUserDataJobOperation")()
            user_data = operation.create
            if row.get("email"):
                identifier = self.sdk.get_type("UserIdentifier")()
                identifier.hashed_email = row["email"]
                user_data.user_identifiers.append(identifier)
            if row.get("phone"):
                identifier = self.sdk.get_type("UserIdentifier")()
                identifier.hashed_phone_number = row["phone"]
                user_data.user_identifiers.append(identifier)
            if user_data.user_identifiers:
                operations.append(operation)
        if not operations:
            return
        self._execute(service.add_offline_user_data_job_operations,
                      resource_name=job_resource, operations=operations,
                      op="audience_job_add")
        self._execute(service.run_offline_user_data_job,
                      resource_name=job_resource, op="audience_job_run")

    # -------------------------------------------------------------- conversions
    def list_conversion_actions(self):
        """UPLOAD_CLICKS conversion actions (for the account config helper)."""
        query = ("SELECT conversion_action.id, conversion_action.name, "
                 "conversion_action.resource_name FROM conversion_action "
                 "WHERE conversion_action.type = 'UPLOAD_CLICKS'")
        actions = []
        for rows in self._search(query, op="list_conversion_actions"):
            actions.extend({"name": row.conversion_action.name,
                            "resource_name": row.conversion_action.resource_name}
                           for row in rows)
        return actions

    def upload_click_conversions(self, conversions):
        """Upload with partial_failure so one bad row surfaces as a validation
        error instead of silently dropping the batch."""
        action = self.account.sudo().conversion_action_resource
        if not action:
            raise AdsApiError("No conversion action configured on the account.",
                              "validation")
        service = self.sdk.get_service("ConversionUploadService")
        rows = []
        for conversion in conversions:
            click = self.sdk.get_type("ClickConversion")()
            if conversion.get("gclid"):
                click.gclid = conversion["gclid"]
            click.conversion_action = action
            click.conversion_date_time = conversion["datetime"]
            click.conversion_value = float(conversion.get("value") or 0.0)
            click.currency_code = conversion.get("currency") or "USD"
            for kind, attr in (("email_hash", "hashed_email"),
                               ("phone_hash", "hashed_phone_number")):
                if conversion.get(kind):
                    identifier = self.sdk.get_type("UserIdentifier")()
                    setattr(identifier, attr, conversion[kind])
                    click.user_identifiers.append(identifier)
            rows.append(click)
        request = self.sdk.get_type("UploadClickConversionsRequest")(
            customer_id=self.customer_id, conversions=rows, partial_failure=True)
        response = self._execute(service.upload_click_conversions,
                                 request=request, op="upload_click_conversions")
        failure = getattr(response, "partial_failure_error", None)
        if failure and getattr(failure, "code", 0):
            raise AdsApiError(f"Conversion upload partial failure: "
                              f"{getattr(failure, 'message', failure)}",
                              "validation")

    # ------------------------------------------------------------------- leads
    #: One GAQL query covers all forms; the engine routes rows per form.
    LEADS_PER_FORM = False

    def list_lead_forms(self):
        query = ("SELECT asset.id, asset.name, asset.lead_form_asset.headline "
                 "FROM asset WHERE asset.type = 'LEAD_FORM'")
        rows = []
        for chunk in self._search(query, op="list_lead_forms"):
            for row in chunk:
                asset = row.asset
                headline = getattr(getattr(asset, "lead_form_asset", None),
                                   "headline", "")
                rows.append({
                    "external_id": str(asset.id),
                    "name": asset.name or headline or f"Lead form {asset.id}",
                    "status": "active",
                    "locale": None,
                    "questions": None,
                    "page_external_id": None,
                })
        return rows

    def iter_leads(self, form_external_id=None, since=None, max_pages=None):
        """Single query over lead_form_submission_data (30-day conservative
        retention). Rows carry form_external_id so the caller routes them."""
        where = ""
        if since:
            literal = since.strftime("%Y-%m-%d %H:%M:%S") if hasattr(
                since, "strftime") else str(since)
            where = (" WHERE lead_form_submission_data.submission_date_time >= "
                     f"{self._gaql_quote(literal)}")
        query = ("SELECT lead_form_submission_data.id, "
                 "lead_form_submission_data.asset, "
                 "lead_form_submission_data.campaign, "
                 "lead_form_submission_data.ad_group, "
                 "lead_form_submission_data.gclid, "
                 "lead_form_submission_data.lead_form_submission_fields, "
                 "lead_form_submission_data.custom_lead_form_submission_fields, "
                 "lead_form_submission_data.submission_date_time "
                 f"FROM lead_form_submission_data{where}")
        for chunk in self._search(query, max_pages=max_pages, op="iter_leads"):
            yield [self._normalize_lead(row) for row in chunk]

    def _normalize_lead(self, row):
        data = row.lead_form_submission_data
        entries = []
        for field in getattr(data, "lead_form_submission_fields", []) or []:
            key = _enum_name(field.field_type).lower()
            entries.append({"key": key, "values": [field.field_value]})
        for field in getattr(data, "custom_lead_form_submission_fields", []) or []:
            entries.append({"key": (field.question_text or "").lower(),
                            "values": [field.field_value]})
        return {
            "external_id": str(data.id),
            "form_external_id": _tail(data.asset) or None,
            "created_time": self._parse_dt(data.submission_date_time),
            "campaign_external_id": _tail(data.campaign) or None,
            "adset_external_id": _tail(data.ad_group) or None,
            "ad_external_id": None,
            "is_organic": False,
            "gclid": data.gclid or None,
            "fields": entries,
            "raw": {"id": str(data.id)},
        }

    # -------------------------------------------------------- normalization
    def _normalize_campaign(self, row):
        campaign = row.campaign
        budget = getattr(row, "campaign_budget", None)
        daily = self._from_micros(getattr(budget, "amount_micros", 0) or 0)
        lifetime = self._from_micros(getattr(budget, "total_amount_micros", 0) or 0)
        channel = _enum_name(campaign.advertising_channel_type)
        return {
            "external_id": str(campaign.id),
            "name": campaign.name or f"Campaign {campaign.id}",
            "status": _STATUS_MAP.get(_enum_name(campaign.status), "paused"),
            "platform_status": _enum_name(campaign.status),
            "objective": _CHANNEL_OBJECTIVE.get(channel, "other"),
            "objective_raw": channel,
            "budget_type": "lifetime" if (lifetime and not daily) else "daily",
            "budget_amount": daily or lifetime,
            "google_budget_resource": str(campaign.campaign_budget or ""),
            "bid_strategy": _enum_name(campaign.bidding_strategy_type),
            "special_ad_categories": [],
            "start_datetime": self._parse_dt(campaign.start_date),
            "stop_datetime": self._parse_dt(campaign.end_date),
            "remote_updated_at": None,
            "provider_data": {"resource_name": campaign.resource_name},
            "raw": {"id": str(campaign.id), "resource_name": campaign.resource_name,
                    "channel": channel},
        }

    def _normalize_ad_group(self, row):
        ad_group = row.ad_group
        return {
            "external_id": str(ad_group.id),
            "campaign_external_id": _tail(ad_group.campaign),
            "name": ad_group.name or f"Ad Group {ad_group.id}",
            "status": _STATUS_MAP.get(_enum_name(ad_group.status), "paused"),
            "platform_status": _enum_name(ad_group.status),
            "budget_type": "none",
            "budget_amount": 0.0,
            "adgroup_type": _enum_name(ad_group.type_),
            "cpc_bid": self._from_micros(ad_group.cpc_bid_micros or 0),
            "remote_updated_at": None,
            "provider_data": {"resource_name": ad_group.resource_name},
            "raw": {"id": str(ad_group.id), "resource_name": ad_group.resource_name},
        }

    def _normalize_ad(self, row):
        wrapper = row.ad_group_ad
        ad = wrapper.ad
        return {
            "external_id": str(ad.id),
            "adset_external_id": _tail(wrapper.ad_group),
            "name": ad.name or f"Ad {ad.id}",
            "status": _STATUS_MAP.get(_enum_name(wrapper.status), "paused"),
            "platform_status": _enum_name(wrapper.status),
            "google_ad_type": _enum_name(ad.type_),
            "final_urls": list(ad.final_urls or []),
            "preview_url": None,
            "creative": None,
            "remote_updated_at": None,
            "provider_data": {"resource_name": wrapper.resource_name},
            "raw": {"id": str(ad.id), "resource_name": wrapper.resource_name},
        }
