# Configurable alerts

Open **Alerts → Add alert** as Admin, Operator, or a custom role with Configure alerts permission.
Viewer accounts can read alerts and history. All configuration changes require a session and CSRF token.

## Saved alerts and routers

Create multiple independent alerts with names such as “Huawei weak signal” and “ZTE tower change”.
Each alert has router-selection checkboxes and its own enabled flag, thresholds and profile fields.
Select one or both routers. Choose **AND** or **OR** between individual signal conditions. AND binds before OR: A AND B OR C
means (A AND B) OR C. Expressions apply within each selected router using values from the same sample. Profile changes,
stale-data warnings and router-action failures trigger separately from the signal group. Save returns directly to **Alerts → Saved alerts**, including when no condition is triggered.
The list shows saved bounds and per-router status (Monitoring, Triggered, Waiting for samples,
Stale samples, Router not configured, Disabled). Edit/delete controls require Configure alerts permission.
Validation failures stay on the form with the submitted entries. Deleting a rule retains its event history.

The previous single configuration is migrated once to **Existing alert rules**, preserving your current
thresholds and selected profile fields, initially for both routers. Edit it to restrict its routers.
Deleted rules are not recreated on pod restart. Rules live in `dashboard_alert_rules`; evaluation state
is independent per rule and router. Editing a rule increments its revision so current breaches are
reevaluated on the next successful sample. Event history identifies the rule name and router.

**Saved alerts** are configuration. **Triggered conditions** and **Event history** show matches:
saving a rule alone does not create a triggered event. For example RSRP −79 dBm does not match
“below −90 dBm”. Profile-only alerts need an initial sample before detecting a change.

## Signal thresholds

Add signal-condition rows for RSRP, RSRQ, SINR, RSSI or CQI. Each row has an enable checkbox,
metric, operator, and threshold. Operators are `<`, `<=`, `==`, `!=`, `>=`, `>`:

- `<` and `>` are exclusive: equality does not match.
- `<=` and `>=` include the threshold value.
- `==` and `!=` compare numeric equality/inequality. Missing values never match, including `!=`.

AND joins adjacent conditions; OR starts an alternative term. For example,
`RSRP <= -100 AND RSRQ < -12 OR SINR <= 4` matches when both the RSRP and RSRQ conditions match,
or when SINR matches by itself. A missing value prevents a new match for its AND term. Unknown data does
not falsely clear an existing group unless another known condition proves it no longer matches.
Use repeated metrics for ranges, e.g. RSRP >= -100 AND RSRP <= -80. Up to 32 rows are supported.
Each matching AND term is recorded as one combined signal event. Disabled conditions do not participate;
the connector on the next enabled row determines how it joins the previous enabled condition.
Existing below/above bounds are displayed as `<`/`>` rows without changing their enabled state;
existing all-AND/all-OR alerts retain their saved behavior. The first connector is hidden and ignored. Site & landing page has no signal settings.

### Router statuses

Monitoring means fresh recorded samples are available. Triggered means the current sample matches
signal conditions or another current warning. Stale samples means the most recent sample is older
than that alert's sample freshness limit (default 15 minutes); it is not an online/offline check.
The list shows the last-sample time. Older samples are excluded from current signal matching even
if stale warnings are disabled. Waiting for samples means no stored data, and Router not configured
means credentials are absent. Router selections are evaluated independently; AND does not combine
values from Huawei with values from ZTE.

## Cell/tower profile changes

Enable profile-change monitoring and select fields: Cell ID, PCI, eNodeB ID, TAC, PLMN, band,
EARFCN, transmission mode, network type, operator, downlink/uplink bandwidth, saved profile label,
region and custom fields. The first known value establishes a baseline; missing values neither
trigger a change nor erase that baseline. Events show old → new values for changed selected fields.
CQI is configured as a numerical threshold rather than a profile-identity field.

## Events and polling

The existing historical polling worker evaluates each successful sample without requiring an open
browser. Persistent state records threshold entry/recovery and profile changes. Repeated breaches
are not repeated events. Shared PostgreSQL locking prevents duplicate evaluation across replicas;
out-of-order samples cannot replace newer state. Missing values do not generate false recoveries.
The page displays current conditions plus the latest 100 recorded events, retained for 30 days.
Historical polling must remain enabled for continuous event capture; no extra monitoring stack is required.
A new configuration takes effect at the next successful sample. This is in-app alerting; external
notification delivery is not configured.

Stale-data warnings (1–1440 minutes) and failed/uncertain router-action notices can also be toggled.
Current-condition views use recorded samples; stale signal values are not reported as fresh breaches.

## Focused validation and release workflow

For routine iterations, run the tests relevant to changed behavior once, and a brief check of the
changed pages. Broaden testing only for a failure or a concrete unresolved concern. Build once,
push the versioned image and latest tag, roll out, and perform one live smoke check. GitHub
publication follows user validation. Avoid repeated full regression, image hash checks and browser
matrices when no new change or failure justifies them.

The focused disposable-DB modules are `resources/test_named_alerts.py` and `resources/test_alert_settings.py`. Never point integration
tests at the lab database. It covers validation, migration, events, role permissions and user creation.

## Notifications tab

**Alerts → Notifications** (`/alerts/notifications`) is available to signed-in users. It provides a
home for future chat/messaging and email channels and delivery history. This release adds navigation
and clearly marked placeholders only: no channels are configured, no provider credentials are
collected, and no outbound notifications are sent. Alert events remain available under Overview.
