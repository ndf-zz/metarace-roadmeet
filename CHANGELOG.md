## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

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
