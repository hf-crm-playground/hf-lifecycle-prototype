# Klaviyo setup for the Trip Decision Service

Generated from the decision table (`python -m nba setup-spec`). The service writes only events and profile properties; everything below is built by a CRM operator.

## 1. Flow: NBA Dispatcher

- Trigger: Metric: NBA Decision; trigger filter: arm equals treatment
- Flow filter: nba_exclude is not true
- Smart Sending: OFF (the Decision Service enforces cap, sub-caps and recency)
- Quiet hours: OFF (the Decision Service holds decisions to the local send window)
- First step: Branch immediately: the service emits the event only when the local send window is open, so no delay or freshness split is needed (expires_at is for audit)
- Tracking: `UTM utm_source=klaviyo&utm_medium=<channel>&utm_campaign=nba_<content_key>&utm_content=<decision_id>`

| Branch condition | content_key | Message |
| --- | --- | --- |
| action = I1 AND channel = push | topup_runout | push: Top up before data runs out |
| action = I1 AND channel = email | topup_runout | email: Top up before data runs out |
| action = I1 AND channel = whatsapp | topup_runout | whatsapp: Top up before data runs out |
| action = I2 AND channel = push | plan_extend | push: Extend or buy a plan for the same destination |
| action = I2 AND channel = email | plan_extend | email: Extend or buy a plan for the same destination |
| action = I3 AND channel = push | always_on_ending | push: Always On data ending: buy a plan |
| action = I3 AND channel = email | always_on_ending | email: Always On data ending: buy a plan |
| action = P1 AND channel = email | activate_help | email: Activation help, device-specific |
| action = P1 AND channel = push | activate_help | push: Activation help, device-specific |
| action = I6 AND channel = push | add_destination | push: Add the new destination |
| action = B1 AND channel = push | always_on_claim | push: Claim this month's 1 GB |
| action = B1 AND channel = email | always_on_claim | email: Claim this month's 1 GB |
| action = I4 AND channel = push | app_balance_intrip | push: Check your balance in the app |
| action = I4 AND channel = email | app_balance_intrip | email: Check your balance in the app |
| action = I5 AND channel = push | feature_intro_intrip | push: Introduce VPN, virtual location or safe browsing |
| action = C2 AND channel = push | capture_window | push: Your eSIM for the destination, ready in the app |
| action = C2 AND channel = email | capture_window | email: Your eSIM for the destination, ready in the app |
| action = T1 AND channel = email | always_on_enrol | email: Claim 1 GB a month with Always On |
| action = T1 AND channel = push | always_on_enrol | push: Claim 1 GB a month with Always On |
| action = T2 AND channel = email | next_destination | email: Your eSIM works in 190+ destinations |
| action = T3 AND channel = email | app_install_posttrip | email: Keep your eSIM and perks in the app |
| action = P2 AND channel = email | app_install_pretrip | email: Install the app to manage the eSIM |
| action = P3 AND channel = push | feature_primer | push: Prime one companion feature (safe browsing) |
| action = I7 AND channel = email | referral_intrip | email: Refer a friend (in-trip) |
| action = T4 AND channel = email | referral_posttrip | email: Refer a friend (post-trip) |
| action = B2 AND channel = email | seasonal_intent | email: Seasonal next-trip prompt |
| action = F1 AND channel = email | trip_feedback | email: Trip feedback, 0-10 score |
| action = R1 AND channel = email | review_invite | email: Public review invitation |

## 2. Segments

| Segment | Definition | Use |
| --- | --- | --- |
| NBA - Global holdout | Properties about someone: nba_holdout_group equals holdout | Exclude from every marketing campaign and from overlapping flows, so the holdout is clean |
| NBA - In-app slot active | nba_inapp_action is set AND nba_inapp_expires_at is after today | The app reads nba_inapp_action through the profile API for its in-app card |
| NBA - Capture allowed | nba_capture_allowed is true AND nba_capture_allowed_until is after now | Flow filter on the abandoned-cart and browse-recovery flows (C1) |
| NBA - Excluded by operator | nba_exclude is true | Audit |

## 3. Profile properties written by the service

| Property | Type | Values |
| --- | --- | --- |
| nba_holdout_group | string | holdout | treatment |
| nba_last_action | string | content_key or none |
| nba_last_decision_id | string | dec_... |
| nba_last_decision_at | date | ISO 8601 |
| nba_last_reason | string | plain sentence, < 200 chars |
| nba_sends_30d_total / _email / _push / _whatsapp | number | after this decision |
| nba_inapp_action, nba_inapp_expires_at | string, date | in-app slot, 7 days |
| nba_capture_allowed, nba_capture_allowed_until | boolean, date | C1 gate, 24 h |
| rating_prompt_allowed | boolean | R2; the app enforces store quotas |
| nba_policy_version | string | v1.0 |
| nba_exclude (operator) | boolean | set by CRM; read by the service |
| nba_manual_action (operator) | string | action id; read by the service |
| trip_* (Trip State Builder) | mixed | see trip_state/klaviyo.py |

## 4. Phase 0: existing flows

| Flow id | Flow | NBA action | Phase 0 |
| --- | --- | --- | --- |
| TWmsiY | CRM_TRIPS_POST_TOP_UP_V2 | I1 | Pause when I1 goes live (one owner per moment); until then add the holdout exclusion |
| RJGDNX | MAFLY06 - Top up Flow | I1 | Pause when I1 goes live (one owner per moment); until then add the holdout exclusion |
| RX3ADw | CRM_TRIPS_ALWAYS_ON_ENDING_TOP_UP_PUSH | I3 | Pause when I3 goes live (one owner per moment); until then add the holdout exclusion |
| UTREx6 | CRM_TRIPS_POST_UPSELLING_V3 | T2 | Pause when T2 goes live (one owner per moment); until then add the holdout exclusion |
| SEhSMh | CRM_TRIPS_POST_WINBACK_ACTIVE_BUYERS_V3 | T2 | Pause when T2 goes live (one owner per moment); until then add the holdout exclusion |
| T2SZRT | CRM_TRIPS_POST_WINBACK_ACTIVE_BUYERS_V2 | T2 | Pause when T2 goes live (one owner per moment); until then add the holdout exclusion |
| ReC6vj | CRM_TRIPS_REACT_WINBACK_AT_RISK_CUSTOMERS | B2 | Pause when B2 goes live (one owner per moment); until then add the holdout exclusion |
| XrinQr | CRM_TRIPS_REACT_WINBACK_DORMANT_CUSTOMERS | B2 | Pause when B2 goes live (one owner per moment); until then add the holdout exclusion |
| YtYkP2 | CRM_TRIPS_REACT_WINBACK_SUPER_DORMANT_CUSTOMERS | B2 | Pause when B2 goes live (one owner per moment); until then add the holdout exclusion |
| SegAKS | CRM_TRIPS_REACT_AT_RISK_SEGMENT_GAMIFY_10OFF | review | Keep; counts toward the cap; add the holdout segment as an exclusion |
| UPDqwa | CRM_TRIPS_REACT_D1_PRE_TRAVEL_REMINDER (date-based) | P1 | Pause when P1 goes live (one owner per moment); until then add the holdout exclusion |
| ULbTxg | CRM_TRIPS_POST_LOYALTY_PLATINUM | review | Keep; counts toward the cap; add the holdout segment as an exclusion |
| VQBu4P | CRM_TRIPS_POST_LOYALTY_GOLD | review | Keep; counts toward the cap; add the holdout segment as an exclusion |
| WDWMtu | CRM_TRIPS_POST_LOYALTY_SILVER | review | Keep; counts toward the cap; add the holdout segment as an exclusion |
| RamEKB | CRM_PLANS_POST_ONBOARDING | review | Keep; counts toward the cap; add the holdout segment as an exclusion |
| SMEfpK | CRM_TRIPS_PRE_ABANDONED_CART_V3 | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| Y3MGDJ | CRM_TRIPS_PRE_ABANDONED_CART_ES | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| X8RSQq | CRM_TRIPS_PRE_ABANDONED_CART_EN | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| YqWmDE | CRM_TRIPS_PRE_ABANDONED_CART_FR | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| RxgHLm | CRM_TRIPS_PRE_ABANDONED_CART_DE | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| Xpnhkr | CRM_TRIPS_PRE_ABANDONED_CART_PT | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| TvFpJJ | CRM_TRIPS_PRE_ABANDONED_CART_APP_BEGIN_CHECKOUT_V2 | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| YkTUXw | CRM_TRIPS_PRE_APP_VIEW_ITEM_RECOVERY_V2 | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| TUTQFa | CRM_TRIPS_PRE_ABANDONED_ORDER_CREATED_V1 | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
| QeUyvL | PRO - Abandoned Cart | C1 | Keep sending; add flow filter: segment 'NBA - Capture allowed' |
