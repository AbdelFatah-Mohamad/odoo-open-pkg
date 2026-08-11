# -*- coding: utf-8 -*-
from . import ads_api
from . import meta_api
from . import google_api

# provider -> client class, resolved here to keep ads_api free of circular imports.
PROVIDER_API = {
    "meta": meta_api.MetaAdsApi,
    "google": google_api.GoogleAdsApi,
}
