# Changelog

All notable changes to Starlink Train Watch. The same notes appear as the release text in HACS.

## v0.3.0 — Stable train IDs, faster launch-date retries

Fixes duplicate reminders and duplicate calendar entries, retries missing launch dates much sooner, and cuts disk writes and state updates. After updating through HACS, restart Home Assistant. Existing settings are kept.

**Action needed if you use the notification blueprint:** the blueprint now remembers `launch group|start time` instead of the train ID. Re-save your automation once after updating; at worst you get one repeat reminder for a train already announced before the update.

### Fixed
- Train IDs stay stable when predictions are recalculated, and two trains from the same launch never share an ID. This removes duplicate reminders and duplicate calendar UIDs.
- The reminder blueprint deduplicates on launch group plus start time (within 20 minutes), so recalculated predictions no longer trigger a second reminder.
- Launch dates that could not be fetched are retried after about 45 minutes instead of up to 6 hours, without re-downloading the orbital feed. Fresh launches no longer stay unpredicted for hours.
- The orbital cache is only written to disk when it changed, instead of every 30 minutes (less wear on SD cards).
- The ephemeris download enforces its size limit on every chunk, and a corrupt or truncated `de421.bsp` is deleted and downloaded again automatically.
- `start_azimuth` now belongs to the satellite that starts first, so "look towards" matches the start time.
- Launch age is counted from the end of the launch's UTC day, so late-evening launches keep their full window.
- Removing the integration now deletes its cache and the ephemeris from `.storage`.
- The satellites list is no longer stored by the recorder, so large trains stay under the attribute size limit.
- Minimum satellites rejects fractional input (3.7) instead of silently truncating it.
- A satellite seen twice in one time window is no longer dropped silently.

### Changed
- Only one integration instance can be configured; Home Assistant now enforces this in the add-integration dialog.
- Time-based entities (next train, visibility, calendar) update at the next start/end boundary instead of every 30 seconds.
- Groups too small to ever form a train are skipped before the orbit calculation.
- Blueprint uses the current entity selector syntax and documents that only the next train is considered.

### Internal
- 21 new regression tests (40 total), each written to fail on v0.2.0 first.
- Test harness no longer logs a misleading Zeroconf warning (it came from Home Assistant's own HTTP session, not this integration).
- Code tidy-ups: top-level imports, unused imports removed.

Not changed: predictions remain geometric estimates, not confirmed sightings or brightness guarantees. Tested with Home Assistant 2026.10.4.

## v0.2.0 — Train filters and satellite pass details

- Configurable minimum predicted satellite count (default 3), with no fixed launch-size maximum.
- Setup and Configure explain satellite counts, peak-time spread and other filters with examples.
- The next-pass sensor includes per-satellite names, NORAD IDs, designators, orbital epochs, predicted windows, directions, elevations, illumination and range/altitude at peak.
- Calendar events list the predicted members and local peak times.

## v0.1.1 — Catalogue category metadata

- Added catalogue category metadata for HACS.

## v0.1.0 — Initial release

- First public release.
