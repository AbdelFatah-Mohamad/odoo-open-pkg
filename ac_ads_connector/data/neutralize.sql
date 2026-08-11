-- Neutralized copies (test/staging restores) must never be able to touch live
-- ad accounts or spend money: wipe every credential and disconnect.
UPDATE ads_account
   SET meta_access_token = NULL,
       meta_page_access_token = NULL,
       meta_app_secret = NULL,
       google_client_secret = NULL,
       google_refresh_token = NULL,
       google_sa_json = NULL,
       google_developer_token = NULL,
       state = 'draft',
       cooldown_until = NULL;

-- No queued mutation may ever fire from a restored copy.
UPDATE ads_push_job SET state = 'cancel' WHERE state IN ('queued', 'in_progress');
