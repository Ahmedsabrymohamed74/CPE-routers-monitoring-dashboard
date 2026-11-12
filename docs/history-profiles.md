# Cellular history profiles and filters

Implements GitHub issues [#25](https://github.com/Ahmedsabrymohamed74/CPE-routers-monitoring-dashboard/issues/25), [#26](https://github.com/Ahmedsabrymohamed74/CPE-routers-monitoring-dashboard/issues/26), and [#27](https://github.com/Ahmedsabrymohamed74/CPE-routers-monitoring-dashboard/issues/27).

Choose a Cell ID, then a PCI/eNodeB sector profile. The second selector shows available PCI, eNodeB and TAC values from the selected historical range. Choose **All sectors (PCI)** to analyze all samples for that Cell ID, or **All cells** for all cells on the selected router. Clicking a cell-group row selects that exact sector. The profile summary includes TAC/eNodeB, sample count and first-seen time. The saved-profile browser also uses Cell ID followed by PCI/eNodeB selection and displays TAC metadata.

Historical groups distinguish Cell ID, PCI, eNodeB and TAC, so observations with the same PCI but a different tower context remain separate. Missing identifiers are normalized to empty strings. First-seen dates still use the entire stored history, while counts and averages use the requested time range. No database schema migration is required; existing saved tags and profile metadata are preserved.

Each RSRP, RSRQ and SINR tooltip starts with **Cell/Tower Profile**, followed by the metric value, average and timestamp. Profile labels are escaped before inserting them in tooltip HTML. LAN tooltips keep their previous contents.

A loading indicator appears for explicit filter requests, but controls remain usable throughout. A changed range, cell, sector or router cancels the previous fetch and rejects stale results even if abort races with the response. Identical in-flight requests share one fetch. Live metric polling refreshes history silently at most once every 30 seconds, skips an already pending request, and preserves unchanged dropdown options. A 15-second deadline clears a stalled request and allows an immediate retry; failed background refreshes preserve the currently displayed history without opening a popup. The Now range reads only its latest matching database row. Resizing the browser redraws cached charts without fetching history again. A filter with no samples stays selected and reports the empty result instead of silently switching to All cells.

## API

`GET /api/history` continues to accept `vendor`, `minutes`, `start`, `end`, `latest`, `limit`, and `cell_id`. It also accepts optional exact-match `pci`, `enodeb_id`, and `tac` filters. Omitting a sector parameter means all values; explicitly passing an empty value matches absent metadata. Values are bound SQL parameters. `cell_groups` includes the full set of range-scoped group choices independently of the selected sector, with `enodeb_id`, `tac`, `profile_enodeb_id`, and `profile_tac` fields.

Example:

```text
/api/history?vendor=huawei&minutes=60&cell_id=100&pci=0&enodeb_id=10&tac=20
```

## Validation

DOM tests (development dependency only; jsdom is not needed by the deployed app):

```sh
npm ci --ignore-scripts
npm test
# Node 16 prints a file-level result with --test; use this for individual case results:
node scripts/test_history_ui.cjs
```

The eight DOM cases cover cascading filters and all-sector selection, empty ranges, usable controls during loading, request coalescing, error recovery, stale responses, escaped tooltips, saved-profile browsing, throttled background refreshes and request timeouts.

The seven database/API integration cases require an **isolated, disposable** TimescaleDB/PostgreSQL database. They truncate only their test database's metrics and tags. They refuse to run unless both `HISTORY_TEST_DATABASE=1` and `DB_NAME=cpe_issues_tests` are set. Supply `DB_HOST`, `DB_PORT`, `DB_USER` and `DB_PASSWORD` for that disposable database, then run with the application dependencies installed:

```sh
cd resources
HISTORY_TEST_DATABASE=1 DB_NAME=cpe_issues_tests python -B -m unittest -v test_history
```

Test coverage includes vendor isolation, zero-valued PCI, missing metadata, parameterized input, all-time first-seen dates, latest/custom time ranges, the authenticated history API, and the rendered dashboard. These tests do not poll routers or require deploying to Kubernetes.
