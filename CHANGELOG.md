## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

## [1.13.13] - 2025-09-16

### Added

   - Configure min and max average speed displayed on reports
   - Add number collection report, sorted by competitor name
   - Add lap colours to team time trial handler
   - Add passing source configuration option to team time trial

### Changed

   - Use call-up for default Team Time Trial start report
   - Use meet min/max average for reports

### Fixed

   - Use bunch time for group result down time instead of elapsed
   - Disable handicap fastest time search when idle or finished

## [1.13.12] - 2025-08-12

### Added

   - new script drelay to relay passings from a single decoder to telegraph
   - return rider to event (irtt)
   - add lap time report when lapinters configured (irtt)

### Changed

   - dnf/dsq/dns riders with start, inters, results are not cleared (irtt)
   - clear time also clears comment field (irtt)
   - estimated arrival time marked only when at least one present (irtt)
   - suppress dnf/dns/dsq riders from arrivals (irtt)
   - announce category lap count at end of lap, including finish
   - only announce rider primary category for arrivals and passing
   - append notes to uciid on rms call-up report instead of overwriting

### Fixed

   - ignore exception when checking for dark mode, eg via fakemeet
   - save and load all print options
   - arrival estimate from inters uses cat distance (irtt)
   - correct transponder trigger assignment when chronometer not in use (irtt)
   - update places when rider times cleared in (irtt)
   - remove double announce rider on finish passing
   - use fastest time with most laps for laplines on laptime report (irtt)
   - fix handling of TreeSelection.get_selected() return values
   - update lap count for riders assigned penalty time via "laps down"

## [1.13.11] - 2025-07-27

### Added

   - quit with error if loadconfig detects trackmeet configuration

### Changed

   - don't alter background of rider no
   - use grey for unseen bg on laps, then colourmap for seen riders
   - check style on load to determine dark or light lap colours
   - alter status line handler to only stack one message from each context
   - don't queue request to scroll log view if a request is already queued

### Fixed

   - reduce priority of timer events to avoid starving main loop
     during a flood of rider passings

## [1.3.10] - 2025-07-23

### Added

   - display print progress on status bar
   - add print method for preview without save to pdf

### Changed

   - add debugging messages to trace export and report printing
   - optionally include lap/split time report from meet properties
   - optionally include arrivals report from meet properties
   - optionally auto-arm finish from event properties
   - set program and application names to match .desktop file
   - set default logo by xdg name instead of file
   - use __version__ instead of VERSION
   - alter IRTT start line channel delay to 1s
   - assign bare start impulse in strictstart mode by matching to rider

### Fixed

   - block export when already in progress to avoid lockup
   - alter start line loading logic to avoid blocked start line
   - sanity check autotime and transponder mode timing options on irtt load

## [1.13.9] - 2025-07-10

### Added

   - dedicated laptime report for cross and circuit
   - include laptime report with cross and circuit result export
   - colour rider number background green when seen or placed
   - set lap column background colour based on lap count

### Changed

   - use seed column from riderdb instead of notes
   - use call-up report for auto cross startlist export
   - include notes in call-up report info column if not blank

### Fixed

   - use id from view model to match edited category with riderdb entry
   - use default category start of zero when not entered
   - suppress superfluous pagebreaks for empty cat and decision reports

## [1.13.8] - 2025-07-02

### Added

   - add changelog
   - add update function to about dialog
   - display duplicate riders and categories in italics
   - add action return option to options dialog
   - restore duplicate rider if conflict resolved
   - add remove rider/cat in event from rider/cat view

### Changed

   - Use single column Name/Organisation in rider and cat views
   - Reset options dialog alterations on cancel/exit

### Fixed

   - Alteration of rider number or category label updated in event

### Security

   - Remove development venv from built package
