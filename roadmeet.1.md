---
title: ROADMEET
section: 1
header: Road Cycle Race Tool
footer: roadmeet 1.13
date: June, 2025
---

# NAME

roadmeet - timing and results for road cycling races

# SYNOPSIS

roadmeet [*PATH*]

roadmeet \--create

roadmeet \--edit-default

# DESCRIPTION

roadmeet opens the meet located at *PATH* and runs
an event handler according to the meet configuration.
Recognised event types:

   - Road Race
   - Circuit (Kermesse, informal criterium)
   - Criterium
   - Handicap
   - Cyclocross
   - Teams Time Trial
   - Individual Time Trial

*PATH* may be the path of the meet folder,
or any file contained within the meet folder.
If no *PATH* is specified, roadmeet will prompt
user to select a meet path, and then open it.

If the \--create option is supplied,
an empty road meet folder is created and opened.

If the \--edit-default option is supplied,
a default configuration editor is run.

# OPTIONS

\--crate
: Create empty meet folder and open

\--edit-default
: Edit default configuration

# FILES

MEET/riders.csv
: Rider, team and category details

MEET/config.json
: Meet configuration

MEET/event.json
: Event data

MEET/mainlogo.svg
: Primary logo for printed report header (optional)

MEET/sublogo.svg
: Secondary logo for printed report header (optional)

MEET/footlogo.svg
: Logo for printed report footer (optional)

MEET/pdf_template.json
: Printed report layout (optional)

MEET/export/
: Startlist and result report folder

$HOME/Documents/metarace
: Default folder for new meets

$HOME/Documents/metarace/default
: Location for shared optional files not found in meet folder

$HOME/Documents/metarace/default/metarace.json
: Default configuration

# SEE ALSO

Online user manual and event examples: 
<https://6-v.org/roadmeet/>

Git repository: <https://github.com/ndf-zz/metarace-roadmeet>

# COPYRIGHT
Copyright (c) 2025 ndf-zz License: MIT
