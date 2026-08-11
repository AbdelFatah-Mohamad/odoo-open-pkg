# -*- coding: utf-8 -*-
"""Provider-agnostic base for the ad-platform SDK clients.

Both providers go through their official SDK (``facebook_business`` for Meta,
``google-ads`` for Google) but every SDK invocation is funnelled through ONE
choke point, :meth:`AdsApiBase._execute`, which owns the concerns the SDKs do
not: persisted rate-limit cooldowns (per account, survives the transaction),
retry with backoff for transient failures, redacted debug logging, and mapping
provider exceptions onto a single ``failure_type`` taxonomy that drives retry
policy everywhere else in the module (sync engine, push queue).

Provider differences that matter here:

  * **Meta** reports usage on *successful* responses via the ``X-App-Usage`` /
    ``X-Business-Use-Case-Usage`` / ``X-FB-Ads-Insights-Throttle`` headers, so
    ``_capture_rate_state`` runs on success AND failure. Budgets/money are in
    the account currency's minor units.
  * **Google** only signals quota in error payloads (``QuotaError`` with a
    ``retry_delay``). Money is in micros (1/1,000,000 of the currency unit).

``failure_type`` vocabulary (superset of ac_whatsapp_connector's):
  network, recoverable, rate_limit, too_much_data, auth, permission,
  validation, unrecoverable, account, unknown.
"""
import hashlib
import logging
import random
import re
import time
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)

#: failure types worth an inline retry inside _execute (short, bounded).
RETRYABLE_TYPES = ("network", "recoverable")
#: rate_limit waits up to this many seconds inline; longer waits persist a
#: cooldown on the account and abort so the cron picks it up later.
INLINE_RATE_LIMIT_MAX = 15
#: hard cap on total inline sleep per _execute call, seconds.
TOTAL_INLINE_SLEEP_CAP = 30


class AdsApiError(Exception):
    """Provider error carrying a machine-readable class that drives retry policy."""

    def __init__(self, message, failure_type="unknown", *, code=None, subcode=None,
                 retry_after=None, http_status=None):
        super().__init__(message)
        self.failure_type = failure_type
        self.code = code
        self.subcode = subcode
        self.retry_after = retry_after  # seconds the caller should back off
        self.http_status = http_status


class AdsApiBase:
    """Shared plumbing; subclasses implement `_build_sdk`, `_map_exception`,
    `_capture_rate_state` and the read/mutate surface."""

    def __init__(self, account):
        account.ensure_one()
        self.account = account
        self.provider = account.provider
        self.decimals = account.currency_id.decimal_places or 2
        self._sdk = None  # built lazily so credential errors surface via _execute

    # ------------------------------------------------------------------ hooks
    def _build_sdk(self):
        raise NotImplementedError()

    def _map_exception(self, exc):
        """Translate a provider/SDK exception into AdsApiError. Must not raise."""
        raise NotImplementedError()

    def _capture_rate_state(self, result_or_exc):
        """Inspect usage signals; return seconds of cooldown to persist or None."""
        return None

    def _check_credentials(self):
        """Raise AdsApiError('account') when local config is incomplete."""
        raise NotImplementedError()

    # ------------------------------------------------------------------- core
    @property
    def sdk(self):
        if self._sdk is None:
            self._check_credentials()
            self._sdk = self._build_sdk()
        return self._sdk

    def _execute(self, fn, *args, op=None, retries=2, **kwargs):
        """Run one SDK call with cooldown pre-flight, retry and error mapping.

        `fn` is a zero-side-effect callable (usually a bound SDK method or a
        small lambda); `op` names it for logs. Never lets a raw SDK exception
        escape: everything becomes AdsApiError.
        """
        self._check_credentials()
        remaining = self.account._in_cooldown()
        if remaining:
            raise AdsApiError(
                "Account is rate-limited for another %s seconds." % remaining,
                "rate_limit", retry_after=remaining)
        op = op or getattr(fn, "__name__", "call")
        slept = 0.0
        attempt = 0
        while True:
            if self.account.debug_logging:
                self.account._log_api("request", f"{self.provider}:{op} args={self._redact(args, kwargs)}")
            try:
                result = fn(*args, **kwargs)
            except AdsApiError:
                raise
            except Exception as exc:  # noqa: BLE001 - mapped right below, never swallowed
                mapped = self._map_exception(exc)
                cooldown = self._capture_rate_state(exc)
                if cooldown:
                    self.account._set_cooldown(cooldown, f"{op}: {mapped}")
                retryable = (
                    mapped.failure_type in RETRYABLE_TYPES
                    or (mapped.failure_type == "rate_limit"
                        and (mapped.retry_after or 0) <= INLINE_RATE_LIMIT_MAX)
                )
                if retryable and attempt < retries and slept < TOTAL_INLINE_SLEEP_CAP:
                    delay = min(2 ** attempt + random.uniform(0, 1),
                                TOTAL_INLINE_SLEEP_CAP - slept)
                    if mapped.retry_after:
                        delay = max(delay, min(mapped.retry_after, INLINE_RATE_LIMIT_MAX))
                    _logger.info("ads %s:%s transient failure (%s), retry in %.1fs",
                                 self.provider, op, mapped.failure_type, delay)
                    time.sleep(delay)
                    slept += delay
                    attempt += 1
                    continue
                if self.account.debug_logging:
                    self.account._log_api("error", f"{self.provider}:{op} -> {mapped}")
                raise mapped from exc
            cooldown = self._capture_rate_state(result)
            if cooldown:
                self.account._set_cooldown(cooldown, f"{op}: usage threshold reached")
            if self.account.debug_logging:
                self.account._log_api("response", f"{self.provider}:{op} ok")
            return result

    _TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")

    def _redact(self, args, kwargs):
        text = f"{args} {kwargs}"[:500]
        return self._TOKEN_RE.sub("***", text)

    # ------------------------------------------------------------ normalizers
    @staticmethod
    def _from_minor(value, decimals=2):
        """Meta money: minor units string/int -> float in currency units."""
        if value in (None, "", False):
            return 0.0
        return int(value) / (10 ** decimals)

    @staticmethod
    def _to_minor(value, decimals=2):
        return int(round(float(value or 0.0) * (10 ** decimals)))

    @staticmethod
    def _from_micros(value):
        if value in (None, "", False):
            return 0.0
        return int(value) / 1_000_000

    @staticmethod
    def _to_micros(value):
        return int(round(float(value or 0.0) * 1_000_000))

    @staticmethod
    def _parse_date(value):
        """'YYYY-MM-DD' (or datetime/date) -> date, or None."""
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _parse_dt(value):
        """ISO-8601 (with or without tz, 'Z' ok) -> naive UTC datetime, or None."""
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def hash_pii(value, kind="email"):
        """Normalize then SHA-256 (audience uploads / Enhanced Conversions).
        Never store the result — hash at push time only."""
        if not value:
            return None
        value = str(value).strip().lower()
        if kind == "phone":
            digits = re.sub(r"\D", "", value)
            value = digits
        elif kind == "email":
            value = value.replace(" ", "")
        else:
            value = re.sub(r"\s+", "", value)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    # ------------------------------------------------------- read interface
    def get_account_info(self):
        raise NotImplementedError()

    def list_campaigns(self, since=None, max_pages=None):
        raise NotImplementedError()

    def list_adsets(self, since=None, max_pages=None):
        raise NotImplementedError()

    def list_ads(self, since=None, max_pages=None):
        raise NotImplementedError()

    def iter_insights(self, level, since, until, max_pages=None):
        raise NotImplementedError()

    def list_lead_forms(self):
        raise NotImplementedError()

    def iter_leads(self, form_external_id=None, since=None, max_pages=None):
        raise NotImplementedError()

    def get_lead(self, lead_external_id):
        raise NotImplementedError()

    # ------------------------------------------------------ mutate interface
    def set_status(self, external_ref, status):
        raise NotImplementedError()

    def set_budget(self, external_ref, daily=None, lifetime=None):
        raise NotImplementedError()

    def create_campaign(self, values):
        raise NotImplementedError()

    def update_campaign(self, external_ref, values):
        raise NotImplementedError()

    def create_adset(self, values):
        raise NotImplementedError()

    def update_adset(self, external_ref, values):
        raise NotImplementedError()

    def create_creative(self, values):
        raise NotImplementedError()

    def create_ad(self, values):
        raise NotImplementedError()

    def update_ad(self, external_ref, values):
        raise NotImplementedError()

    def create_audience(self, values):
        raise NotImplementedError()

    def upload_audience_users(self, audience_ref, schema, hashed_rows):
        raise NotImplementedError()

    def upload_click_conversions(self, conversions):
        raise NotImplementedError()
