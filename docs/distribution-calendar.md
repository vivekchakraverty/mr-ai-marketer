# Distribute: the next 30 days

The calendar appears above Send history and covers today through today + 29 days
in the viewer's local timezone. Country and state/region choices are supplied by
the user and remembered locally. An unset country shows international observances
only. A chosen country can show national events, one subdivision, or all supported
subdivisions. Worldwide includes all supported countries and subdivisions.

## Event sources

- The pinned `holidays==0.106` package supplies calculated public, optional,
  unofficial and religious holidays for its supported countries and subdivisions.
  Workday adjustments, bank-only closures, school-only closures and half-day
  categories are excluded. English is requested where the dataset supports it.
- A bundled annual catalogue uses the [United Nations observance list](https://www.un.org/en/observances/list-days-weeks).
  Movable weekdays are calculated for the requested year, rather than copying a
  year-specific date displayed on that page. Multi-day weeks appear on every day
  of their range. This catalogue is maintained with the app, not fetched live.
- [Kannada Rajyotsava](https://bengaluruurban.nic.in/en/festival/kannada-rajyotsava/)
  is included as an annual Karnataka celebration even when a government closure
  list omits it because it falls on Sunday.

Every event retains its source, country and regional coverage. Equal country/date/name
regional entries are grouped; events already in the national catalogue are not
repeated for every subdivision. "National" denotes an entry in the country-level
catalogue, including optional observances; it does not mean a compulsory public
holiday or participation by every person in that country.

Coverage is broad, but cannot enumerate every local festival, denomination or
community celebration. Estimated-date labels remain visible. A dataset update is
needed for new official announcements and changed holiday rules. Dates and source
coverage should be checked before a campaign is scheduled.

The calendar itself operates offline: it does not upload user location selections
to an external event service or require a paid holiday API key. Source links open
only when selected.

## Scheduled posts

Green day badges count active `scheduled` and `scheduled_cloud` jobs. The separate
calendar query includes all active jobs in the date window, regardless of the
100-entry history limit. Bounds are sent as timezone-aware instants and are
inclusive at the start, exclusive at the next day after the 30-day window.

The renderer groups scheduled instants into local dates, preserving DST and date
changes across timezones. Selecting a date shows the channel and local posting
time. Completed, failed and cancelled jobs do not receive green badges.

Markers refresh every 15 seconds and on window focus, successful scheduling, or
schedule deletion. Midnight updates the calendar's date window while it remains
open. A failed refresh is displayed and can be retried. Location changes discard
old event data, and late responses for old selections are ignored.

## Implementation and checks

- Service: `backend/app/services/distribution_calendar.py`.
- API: `GET /distribution/calendar/locations`, `/events`, and `/posts`.
- UI: `electron/src/renderer/src/components/DistributionCalendar.tsx`.
- Date helpers: `distributionCalendarDates.ts` beside the component.
- Packaging collects all holiday country modules and translations.

Run from `backend/`:

```powershell
.venv/Scripts/python.exe -m pytest app/tests/test_distribution_calendar.py app/tests/test_distribution_scheduling.py app/tests/test_distribution_retry.py -q
```

Run from `electron/`:

```powershell
npm run typecheck
npm run verify:calendar
npm run build
```
