# SPDX-License-Identifier: MIT
"""Road mass-start, crit, categorised and handicap handler for roadmeet."""

import os
import gi
import logging
import threading
import bisect

gi.require_version("GLib", "2.0")
from gi.repository import GLib

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

import metarace
from metarace import tod
from metarace import riderdb
from metarace import strops
from metarace import countback
from metarace import report
from metarace import jsonconfig
from . import uiutil

_log = logging.getLogger('rms')
_log.setLevel(logging.DEBUG)

# Model columns

# basic infos
COL_BIB = 0
COL_NAMESTR = 1
COL_SHORTNAME = 2
COL_CAT = 3
COL_COMMENT = 4
COL_INRACE = 5  # boolean in the event
COL_PLACE = 6  # Place assigned in result
COL_LAPS = 7  # Incremented if COL_INRACE and not finished
COL_SEED = 8  # Seeding number (overrides startlist ordering)

# timing infos
COL_RFTIME = 9  # one-off finish time by rfid
COL_CBUNCH = 10  # computed bunch time   -> derived from rftime
COL_MBUNCH = 11  # manual bunch time     -> manual overrive
COL_STOFT = 12  # start time 'offset' - only reported in result
COL_BONUS = 13
COL_PENALTY = 14
COL_RFSEEN = 15  # list of tods this rider 'seen' by rfid

# listview column nos (used for hiding)
CATCOLUMN = 2
COMCOLUMN = 3
INCOLUMN = 4
LAPCOLUMN = 5
SEEDCOLUMN = 6
STARTCOLUMN = 7
BUNCHCOLUMN = 8

ROADRACE_TYPES = {
    'road': 'Road Race',
    'circuit': 'Circuit',
    'criterium': 'Criterium',
    'handicap': 'Handicap',
    'cross': 'Cyclocross',
    'irtt': 'Individual Time Trial',
    'trtt': 'Team Time Trial',
}

# rider commands
RIDER_COMMANDS_ORD = [
    'add', 'del', 'que', 'dns', 'otl', 'wd', 'dnf', 'dsq', 'dec', 'ret', 'man',
    '', 'fin'
]  # then intermediates...
RIDER_COMMANDS = {
    'dns': 'Did not start',
    'otl': 'Outside time limit',
    'dnf': 'Did not finish',
    'wd': 'Withdraw',
    'dsq': 'Disqualify',
    'add': 'Add starters',
    'del': 'Remove starters',
    'que': 'Query riders',
    'fin': 'Final places',
    'dec': 'Add decision',
    'ret': 'Return to event',
    'man': 'Manual passing',
    '': '',
}
_DNFLABELS = {
    'did not start': 'dns',
    'did not finish': 'dnf',
    'withdrawn': 'wd',
    'outside time limit': 'otl',
    'disqualify': 'dsq',
}

RESERVED_SOURCES = [
    'fin',  # finished stage
    'reg',  # registered to stage
    'start',  # started stage
]

DNFCODES = ['otl', 'wd', 'dsq', 'dnf', 'dns']
GAPTHRESH = tod.tod('1.12')
MINPASSTIME = tod.tod(20)
MAXELAP = tod.tod('12h00:00')

# timing keys
key_announce = 'F4'
key_armstart = 'F5'
key_armlap = 'F6'
key_placesto = 'F7'  # fill places to selected rider
key_appendplace = 'F8'  # append sepected rider to places
key_armfinish = 'F9'
key_raceover = 'F10'

# extended fn keys      (ctrl + key)
key_abort = 'F5'
key_clearfrom = 'F7'  # clear places on selected rider and all following
key_clearplace = 'F8'  # clear rider from place list

# config version string
EVENT_ID = 'rms-4.2'

_CONFIG_SCHEMA = {
    'etype': {
        'prompt': 'Roadrace/Criterium/Cyclocross',
        'control': 'section'
    },
    'categories': {
        'prompt': 'Categories:',
        'hint': 'Startlist and result categories',
        'defer': True,
        'default': '',
    },
    'minlap': {
        'prompt': 'Minimum Lap:',
        'control': 'short',
        'places': 1,
        'type': 'tod',
        'hint': 'Reject laps shorter than minimum lap time',
        'attr': 'minlap',
        'default': MINPASSTIME,
    },
    'totlaps': {
        'prompt': 'Laps:',
        'control': 'short',
        'type': 'int',
        'attr': 'totlaps',
        'subtext': '(Cat laps override)',
        'hint': 'Default target number of laps for event',
    },
    'passingsource': {
        'prompt': 'Loop ID:',
        'control': 'short',
        'type': 'int',
        'attr': 'passingsource',
        'hint': 'Loop ID for valid passings',
    },
    'autofinish': {
        'prompt': 'Finish:',
        'control': 'check',
        'type': 'bool',
        'attr': 'autofinish',
        'subtext': 'Automatically Finish?',
        'hint': 'Automatically finish riders on target lap',
        'default': True,
    },
    'autoexport': {
        'prompt': 'Export:',
        'control': 'check',
        'type': 'bool',
        'attr': 'autoexport',
        'subtext': 'Automatically export?',
        'hint': 'Export result automatically',
        'default': False,
    },
    'showdowntimes': {
        'prompt': 'Down Times:',
        'control': 'check',
        'type': 'bool',
        'attr': 'showdowntimes',
        'subtext': 'Show on result?',
        'hint': 'Display down times on result',
        'default': True,
    },
    'dofastestlap': {
        'prompt': 'Fastest Lap:',
        'control': 'check',
        'type': 'bool',
        'attr': 'dofastestlap',
        'subtext': 'Report with result?',
        'hint': 'Report fastest lap time with categorised result',
        'default': False,
    },
    'hcpcatres': {
        'prompt': 'Handicap:',
        'control': 'check',
        'type': 'bool',
        'attr': 'hcpcatres',
        'subtext': 'Show group results?',
        'hint': 'Display group placings on result report',
        'default': False,
    },
    'timelimit': {
        'prompt': 'Time Limit:',
        'control': 'short',
        'attr': 'timelimit',
        'hint': 'Time limit eg: 12%  +1:23  4h00:00',
    },
    'gapthresh': {
        'prompt': 'Time Gap:',
        'control': 'short',
        'type': 'tod',
        'places': 2,
        'hint': 'Threshold for automatic time gap insertion',
        'attr': 'gapthresh',
        'default': GAPTHRESH,
    },
    'clubmode': {
        'prompt': 'Club Mode:',
        'control': 'check',
        'type': 'bool',
        'attr': 'clubmode',
        'subtext': 'Add starters by transponder passing?',
        'hint': 'Add riders to event on passing',
        'default': False,
    },
    'allowspares': {
        'prompt': 'Spares:',
        'control': 'check',
        'type': 'bool',
        'attr': 'allowspares',
        'subtext': 'Record spare bike passings?',
        'hint': 'Add spare bike passings to event as placeholders',
        'default': False,
    },
}


class rms:
    """Road race handler."""

    def hidecolumn(self, target, visible=False):
        tc = self.view.get_column(target)
        if tc:
            tc.set_visible(visible)

    def loadcats(self, cats=[]):
        # clear old cats and reset reserved sources
        self.cats = []  # clear old cat list
        self.catplaces = {}
        self.reserved_sources = [i for i in RESERVED_SOURCES]

        if 'AUTO' in cats:  # ignore any others and re-load from rdb
            self.cats = list(self.meet.rdb.listcats())
            self.autocats = True
        else:
            self.autocats = False
            for cat in cats:
                if cat != '':
                    cat = cat.upper()
                    if cat not in ('CAT', 'SPARE', 'TEAM', 'DS'):
                        self.cats.append(cat)
                    else:
                        _log.warning('Invalid result category: %s', cat)
        self.cats.append('')  # always include one empty cat

        # update reserved sources list with any cat finish labels
        if len(self.cats) > 1:
            for cat in self.cats:
                if cat:
                    srcid = cat.lower() + 'fin'
                    self.reserved_sources.append(srcid)
                    self.catplaces[srcid] = cat

        _log.debug('Result categories: %r; Reserved sources: %r', self.cats,
                   self.reserved_sources)

    def downtimes(self, show):
        """Set the downtimes flag"""
        _log.debug('Set showdowntimes to: %r', show)
        self.showdowntimes = show

    def loadstageinters(self, cr, section):
        """Load stage bonuses, points and awards from the config"""

        # load intermediates
        for i in cr.get(section, 'intermeds'):
            if i in self.reserved_sources:
                _log.info('Ignoring reserved intermed: %r', i)
            else:
                crkey = 'intermed_' + i
                descr = ''
                places = ''
                km = None
                doshow = False
                abbr = ''
                if cr.has_option(crkey, 'descr'):
                    descr = cr.get(crkey, 'descr')
                if cr.has_option(crkey, 'dist'):
                    km = cr.get_float(crkey, 'dist', None)
                if cr.has_option(crkey, 'abbr'):
                    abbr = cr.get(crkey, 'abbr')
                if cr.has_option(crkey, 'show'):
                    doshow = cr.get_bool(crkey, 'show')
                if cr.has_option(crkey, 'places'):
                    places = strops.reformat_placelist(cr.get(crkey, 'places'))
                if i not in self.intermeds:
                    _log.debug('Adding intermed %r: %r %r', i, descr, places)
                    self.intermeds.append(i)
                    self.intermap[i] = {
                        'descr': descr,
                        'places': places,
                        'abbr': abbr,
                        'dist': km,
                        'show': doshow
                    }
                else:
                    _log.info('Ignoring duplicate intermed: %r', i)

        # load contest meta data
        tallyset = set()
        for i in cr.get(section, 'contests'):
            if i not in self.contests:
                self.contests.append(i)
                self.contestmap[i] = {}
                crkey = 'contest_' + i
                tally = ''
                if cr.has_option(crkey, 'tally'):
                    tally = cr.get(crkey, 'tally')
                    if tally:
                        tallyset.add(tally)
                self.contestmap[i]['tally'] = tally
                descr = i
                if cr.has_option(crkey, 'descr'):
                    descr = cr.get(crkey, 'descr')
                    if descr == '':
                        descr = i
                self.contestmap[i]['descr'] = descr
                labels = []
                if cr.has_option(crkey, 'labels'):
                    labels = cr.get(crkey, 'labels').split()
                self.contestmap[i]['labels'] = labels
                source = i
                if cr.has_option(crkey, 'source'):
                    source = cr.get(crkey, 'source')
                    if source == '':
                        source = i
                self.contestmap[i]['source'] = source
                bonuses = []
                if cr.has_option(crkey, 'bonuses'):
                    for bstr in cr.get(crkey, 'bonuses').split():
                        bt = tod.mktod(bstr)
                        if bt is None:
                            _log.info('Invalid bonus %r in contest %r', bstr,
                                      i)
                            bt = tod.ZERO
                        bonuses.append(bt)
                self.contestmap[i]['bonuses'] = bonuses
                points = []
                if cr.has_option(crkey, 'points'):
                    pliststr = cr.get(crkey, 'points').strip()
                    if pliststr and tally == '':
                        _log.error('No tally for points in contest %r', i)
                        tallyset.add('')  # add empty placeholder
                    for pstr in pliststr.split():
                        pt = 0
                        try:
                            pt = int(pstr)
                        except Exception:
                            _log.info('Invalid points %r in contest %r', pstr,
                                      i)
                        points.append(pt)
                self.contestmap[i]['points'] = points
                allsrc = False  # all riders in source get same pts
                if cr.has_option(crkey, 'all_source'):
                    allsrc = cr.get_bool(crkey, 'all_source')
                self.contestmap[i]['all_source'] = allsrc
                self.contestmap[i]['category'] = 0
                if cr.has_option(crkey, 'category'):  # for climbs
                    self.contestmap[i]['category'] = cr.get_posint(
                        crkey, 'category')
            else:
                _log.info('Ignoring duplicate contest %r', i)

            # check for invalid allsrc
            if self.contestmap[i]['all_source']:
                if (len(self.contestmap[i]['points']) > 1
                        or len(self.contestmap[i]['bonuses']) > 1):
                    _log.info('Ignoring extra points/bonus for allsrc %r', i)

        # load points tally meta data
        tallylist = cr.get(section, 'tallys')
        # append any 'missing' tallys from points data errors
        for i in tallyset:
            if i not in tallylist:
                _log.debug('Adding missing tally to config %r', i)
                tallylist.append(i)
        # then scan for meta data
        for i in tallylist:
            if i not in self.tallys:
                self.tallys.append(i)
                self.tallymap[i] = {}
                self.points[i] = {}
                self.pointscb[i] = {}
                crkey = 'tally_' + i
                descr = ''
                if cr.has_option(crkey, 'descr'):
                    descr = cr.get(crkey, 'descr')
                self.tallymap[i]['descr'] = descr
                keepdnf = False
                if cr.has_option(crkey, 'keepdnf'):
                    keepdnf = cr.get_bool(crkey, 'keepdnf')
                self.tallymap[i]['keepdnf'] = keepdnf
            else:
                _log.info('Ignoring duplicate points tally %r', i)

    def loadconfig(self):
        """Load event config from disk."""
        self.ridernos.clear()
        self.riders.clear()
        self.resettimer()
        self.cats = []
        if self.etype == 'criterium':
            _CONFIG_SCHEMA['showdowntimes']['default'] = False
        cr = jsonconfig.config({
            'rms': {
                'start': None,
                'finish': None,
                'finished': False,
                'places': '',
                'decisions': [],
                'hidecols': [INCOLUMN],
                'intermeds': [],
                'contests': [],
                'tallys': [],
                'lapstart': None,
                'lapfin': None,
                'curlap': -1,
                'onlap': 1,
                'passlabels': {},
                'catonlap': {},
                'passingsource': None,
                'laptimes': [],
                'startlist': '',
            }
        })
        cr.add_section('rms', _CONFIG_SCHEMA)
        cr.add_section('riders')
        cr.add_section('stagebonus')
        cr.add_section('stagepenalty')
        cr.merge(metarace.sysconf, 'rms')
        if not cr.load(self.configfile):
            _log.info('Config %s not read, loading defaults', self.configfile)
        cr.export_section('rms', self)

        # load result categories
        self.loadcats(cr.get_value('rms', 'categories').upper().split())

        self.passlabels = cr.get('rms', 'passlabels')
        self.catonlap = cr.get('rms', 'catonlap')
        self.passingsource = cr.get('rms', 'passingsource')

        # check gapthresh
        if self.gapthresh != GAPTHRESH:
            _log.warning('Set time gap threshold %s',
                         self.gapthresh.rawtime(2))
        _log.debug('Minimum lap time: %s', self.minlap.rawtime(1))

        # restore stage inters, points and bonuses
        self.loadstageinters(cr, 'rms')

        # load competitors
        starters = strops.riderlist_split(
            cr.get('rms', 'startlist').upper().strip(), self.meet.rdb)

        onestoft = False
        oneseed = False
        for r in starters:
            ri = self.addrider(r)
            if ri is not None:
                nr = Gtk.TreeModelRow(self.riders, ri)
                if cr.has_option('riders', r):
                    # bib = comment,in,laps,rftod,mbunch,rfseen...
                    ril = cr.get('riders', r)  # rider op is vec
                    lr = len(ril)
                    if lr > 0:
                        nr[COL_COMMENT] = ril[0]
                    if lr > 1:
                        nr[COL_INRACE] = strops.confopt_bool(ril[1])
                    if lr > 2:
                        nr[COL_LAPS] = strops.confopt_posint(ril[2])
                    if lr > 3:
                        evtseed = strops.confopt_posint(ril[3], 0)
                        if evtseed > 0:
                            nr[COL_SEED] = evtseed
                        if nr[COL_SEED] != 0:
                            oneseed = True
                    if lr > 4:
                        nr[COL_RFTIME] = tod.mktod(ril[4])
                    if lr > 5:
                        nr[COL_MBUNCH] = tod.mktod(ril[5])
                    if lr > 6:
                        nr[COL_STOFT] = tod.mktod(ril[6])
                        if nr[COL_STOFT] is not None:
                            onestoft = True
                    if lr > 7:
                        for i in range(7, lr):
                            laptod = tod.mktod(ril[i])
                            if laptod is not None:
                                nr[COL_RFSEEN].append(laptod)
                # record any extra bonus/penalty to rider model
                if cr.has_option('stagebonus', r):
                    nr[COL_BONUS] = cr.get_tod('stagebonus', r)
                if cr.has_option('stagepenalty', r):
                    nr[COL_PENALTY] = cr.get_tod('stagepenalty', r)

        self.laptimes = cr.get('rms', 'laptimes')
        self.set_start(cr.get_tod('rms', 'start'))
        self.set_finish(cr.get_tod('rms', 'finish'))
        self.lapstart = cr.get_tod('rms', 'lapstart')
        self.lapfin = cr.get_tod('rms', 'lapfin')

        self.curlap = cr.get_int('rms', 'curlap', -1)
        self.onlap = cr.get_int('rms', 'onlap', 1)
        self.totlaps = cr.get_int('rms', 'totlaps', None)
        self.places = strops.reformat_placelist(cr.get('rms', 'places'))
        self.decisions = cr.get('rms', 'decisions')
        if cr.get_bool('rms', 'finished'):
            self.set_finished()
        self.recalculate()

        self.hidecols = cr.get('rms', 'hidecols')
        for col in self.hidecols:
            target = strops.confopt_posint(col)
            if target is not None:
                self.hidecolumn(target)

        # load starts and targets and then handle lap situation
        self.load_cat_data()
        if self.etype != 'handicap':
            for c in self.catstarts:
                if self.catstarts[c] is not None:
                    onestoft = True
        else:
            # don't autohide the start column for handicaps
            onestoft = True

        # auto-hide the start column
        if not onestoft:
            self.hidecolumn(STARTCOLUMN)

        # auto-hide the seed column
        if not oneseed:
            self.hidecolumn(SEEDCOLUMN)

        if self.curlap is not None and self.curlap >= 0:
            self.lapentry.set_text(str(self.curlap))
        else:
            self.lapentry.set_text('')
        if self.autofinish:
            self.lapentry.set_sensitive(False)
        else:
            self.lapentry.set_sensitive(True)

        if self.totlaps is not None:
            self.totlapentry.set_text(str(self.totlaps))

        # After load complete - check config and report.
        eid = cr.get_value('rms', 'id')
        if eid is not None and eid != EVENT_ID:
            _log.info('Event config mismatch: %r != %r', eid, EVENT_ID)
            self.readonly = True

    def get_ridercmdorder(self):
        """Return rider command list order."""
        ret = RIDER_COMMANDS_ORD[0:]
        for i in self.intermeds:
            ret.append(i)
        return ret

    def get_ridercmds(self):
        """Return a dict of rider bib commands for container ui."""
        ret = {}
        for k in RIDER_COMMANDS:
            ret[k] = RIDER_COMMANDS[k]
        for k in self.intermap:
            descr = k
            if self.intermap[k]['descr']:
                descr = k + ' : ' + self.intermap[k]['descr']
            ret[k] = descr
        return ret

    def get_startlist(self):
        """Return a list of all rider numbers registered to event."""
        ret = []
        for r in self.riders:
            ret.append(r[COL_BIB])
        return ' '.join(ret)

    def get_starters(self):
        """Return a list of riders that 'started' the event."""
        ret = []
        for r in self.riders:
            if r[COL_COMMENT] != 'dns' or r[COL_INRACE]:
                ret.append(r[COL_BIB])
        return ' '.join(ret)

    def get_catlist(self):
        """Return the ordered list of categories."""
        rvec = []
        for cat in self.cats:
            if cat != '':
                rvec.append(cat)
        return rvec

    def ridercat(self, cat):
        """Return an event result category for the provided rider cat."""
        ret = ''
        checka = cat.upper()
        if checka in self.cats:
            ret = checka
        return ret

    def savestageinters(self, cw, section):
        """Save stage bonuses, intermediates and awards to config"""

        # save intermediate data
        opinters = []
        for i in self.intermeds:
            crkey = 'intermed_' + i
            cw.add_section(crkey)
            cw.set(crkey, 'descr', self.intermap[i]['descr'])
            cw.set(crkey, 'places', self.intermap[i]['places'])
            cw.set(crkey, 'show', self.intermap[i]['show'])
            if 'dist' in self.intermap[i]:
                cw.set(crkey, 'dist', self.intermap[i]['dist'])
            if 'abbr' in self.intermap[i]:
                cw.set(crkey, 'abbr', self.intermap[i]['abbr'])
            opinters.append(i)
        cw.set(section, 'intermeds', opinters)

        # save contest data
        cw.set(section, 'contests', self.contests)
        for i in self.contests:
            crkey = 'contest_' + i
            cw.add_section(crkey)
            cw.set(crkey, 'tally', self.contestmap[i]['tally'])
            cw.set(crkey, 'source', self.contestmap[i]['source'])
            cw.set(crkey, 'all_source', self.contestmap[i]['all_source'])
            if 'category' in self.contestmap[i]:
                cw.set(crkey, 'category', self.contestmap[i]['category'])
            blist = []
            for b in self.contestmap[i]['bonuses']:
                blist.append(b.rawtime(0))
            cw.set(crkey, 'bonuses', ' '.join(blist))
            plist = []
            for p in self.contestmap[i]['points']:
                plist.append(str(p))
            cw.set(crkey, 'points', ' '.join(plist))
        # save tally data
        cw.set(section, 'tallys', self.tallys)
        for i in self.tallys:
            crkey = 'tally_' + i
            cw.add_section(crkey)
            cw.set(crkey, 'descr', self.tallymap[i]['descr'])
            cw.set(crkey, 'keepdnf', self.tallymap[i]['keepdnf'])

    def saveconfig(self):
        """Save event config to disk."""
        if self.readonly:
            _log.error('Attempt to save readonly event')
            return
        cw = jsonconfig.config()
        cw.add_section('rms', _CONFIG_SCHEMA)
        cw.import_section('rms', self)
        cw.set('rms', 'start', self.start)
        cw.set('rms', 'finish', self.finish)
        cw.set('rms', 'lapstart', self.lapstart)
        cw.set('rms', 'lapfin', self.lapfin)
        cw.set('rms', 'finished', self.timerstat == 'finished')
        cw.set('rms', 'places', self.places)
        cw.set('rms', 'curlap', self.curlap)
        cw.set('rms', 'onlap', self.onlap)
        cw.set('rms', 'passlabels', self.passlabels)
        cw.set('rms', 'catonlap', self.catonlap)
        cw.set('rms', 'passingsource', self.passingsource)
        cw.set('rms', 'laptimes', self.laptimes)

        # save stage inters, points and bonuses
        self.savestageinters(cw, 'rms')

        # save riders
        evtriders = self.get_startlist()
        if evtriders:
            cw.set('rms', 'startlist', self.get_startlist())
        else:
            if self.autostartlist is not None:
                cw.set('rms', 'startlist', self.autostartlist)
        if self.autocats:
            cw.set('rms', 'categories', 'AUTO')
        else:
            cw.set('rms', 'categories', ' '.join(self.get_catlist()).strip())
        cw.set('rms', 'decisions', self.decisions)
        cw.set('rms', 'hidecols', self.hidecols)

        cw.add_section('riders')
        # sections for commissaire awarded bonus/penalty
        cw.add_section('stagebonus')
        cw.add_section('stagepenalty')
        for r in self.riders:
            rt = ''
            if r[COL_RFTIME] is not None:
                rt = r[COL_RFTIME].rawtime()  # Don't truncate
            mb = ''
            if r[COL_MBUNCH] is not None:
                mb = r[COL_MBUNCH].rawtime(0)  # But bunch is to whole sec
            sto = ''
            if r[COL_STOFT] is not None:
                sto = r[COL_STOFT].rawtime()
            # bib = comment,in,laps,rftod,mbunch,stoft,seen...
            bib = r[COL_BIB]
            slice = [
                r[COL_COMMENT], r[COL_INRACE], r[COL_LAPS], r[COL_SEED], rt,
                mb, sto
            ]
            for t in r[COL_RFSEEN]:
                if t is not None:
                    slice.append(t.rawtime())  # retain 'precision' here too
            cw.set('riders', bib, slice)
            if r[COL_BONUS] is not None:
                cw.set('stagebonus', bib, r[COL_BONUS])
            if r[COL_PENALTY] is not None:
                cw.set('stagepenalty', bib, r[COL_PENALTY])
        cw.set('rms', 'id', EVENT_ID)
        _log.debug('Saving event config to %s', self.configfile)
        with metarace.savefile(self.configfile) as f:
            cw.write(f)

    def show(self):
        """Show event container."""
        self.frame.show()

    def hide(self):
        """Hide event container."""
        self.frame.hide()

    def title_close_clicked_cb(self, button, entry=None):
        """Close and save the event."""
        self.meet.close_event()

    def set_titlestr(self, titlestr=None):
        """Update the title string label."""
        if titlestr is None or titlestr == '':
            if self.etype in ROADRACE_TYPES:
                titlestr = '[' + ROADRACE_TYPES[self.etype] + ']'
            else:
                titlestr = '[Road Event]'
        self.title_namestr.set_text(titlestr)

    def destroy(self):
        """Emit destroy signal to event handler."""
        if self.context_menu is not None:
            self.context_menu.destroy()
        self.frame.destroy()

    def points_report(self):
        """Return the points tally report."""
        ret = []
        cnt = 0
        for tally in self.tallys:
            sec = report.section('points-' + tally)
            sec.heading = tally.upper() + ' ' + self.tallymap[tally]['descr']
            sec.units = 'pt'
            tallytot = 0
            aux = []
            for bib in self.points[tally]:
                r = self.getrider(bib)
                tallytot += self.points[tally][bib]
                aux.append((self.points[tally][bib], self.pointscb[tally][bib],
                            -strops.riderno_key(bib), -cnt, [
                                None, r[COL_BIB], r[COL_NAMESTR],
                                strops.truncpad(str(self.pointscb[tally][bib]),
                                                16,
                                                ellipsis=True), None,
                                str(self.points[tally][bib])
                            ]))
                cnt += 1
            aux.sort(reverse=True)
            for r in aux:
                sec.lines.append(r[4])
            _log.debug('Total points for %r: %r', tally, tallytot)
            ret.append(sec)

        # collect bonus and penalty totals
        aux = []
        cnt = 0
        onebonus = False
        onepenalty = False
        for r in self.riders:
            bib = r[COL_BIB]
            bonus = 0
            penalty = 0
            intbonus = 0
            total = tod.mkagg(0)
            if r[COL_BONUS] is not None:
                bonus = r[COL_BONUS]
                onebonus = True
            if r[COL_PENALTY] is not None:
                penalty = r[COL_PENALTY]
                onepenalty = True
            if bib in self.bonuses:
                intbonus = self.bonuses[bib]
            total = total + bonus + intbonus - penalty
            if total != 0:
                bonstr = ''
                if bonus != 0:
                    bonstr = str(bonus.as_seconds())
                penstr = ''
                if penalty != 0:
                    penstr = str(-(penalty.as_seconds()))
                totstr = str(total.as_seconds())
                aux.append(
                    (total, -strops.riderno_key(bib), -cnt,
                     [None, bib, r[COL_NAMESTR], bonstr, penstr, totstr]))
                cnt += 1
        sec = report.section('bonus')
        sec.heading = 'Time Bonuses'
        sec.units = 'sec'
        if len(aux) > 0:
            aux.sort(reverse=True)
            for r in aux:
                sec.lines.append(r[3])
            if onebonus or onepenalty:
                bhead = ''
                if onebonus:
                    bhead = 'Stage Bonus'
                phead = ''
                if onepenalty:
                    phead = 'Penalties'
                sec.colheader = [None, None, None, bhead, phead, 'Total']
        ret.append(sec)

        return ret

    def reorder_startlist(self, callup=False):
        """Reorder riders for a startlist."""
        self.calcset = False
        aux = []
        cnt = 0
        if callup:
            for r in self.riders:
                rseed = 9999
                if r[COL_SEED] > 0:
                    rseed = r[COL_SEED]
                riderno = strops.riderno_key(r[COL_BIB])
                aux.append((rseed, riderno, cnt))
                cnt += 1
        else:
            for r in self.riders:
                riderno = strops.riderno_key(r[COL_BIB])
                aux.append((riderno, 0, cnt))
                cnt += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[2] for a in aux])
        return cnt

    def signon_report(self):
        """Return a signon report."""
        ret = []
        self.reorder_startlist()
        if len(self.cats) > 1:
            _log.debug('Preparing categorised signon for %r', self.cats)
            for c in self.cats:
                c = self.ridercat(c)
                _log.debug('Signon Cat %s', c)
                if True:
                    sec = report.signon_list('signon')
                    sec.heading = c
                    dbr = self.meet.rdb.get_rider(c, 'cat')
                    if dbr is not None:
                        sec.heading = dbr['title']
                        sec.subheading = dbr['subtitle']
                        sec.footer = dbr['footer']
                    elif c == '':
                        sec.heading = 'Uncategorised Riders'
                    for r in self.riders:
                        # primary cat is used for sign-on
                        cs = r[COL_CAT]
                        rcat = self.ridercat(riderdb.primary_cat(cs))
                        if rcat == c:
                            cmt = None
                            if not r[COL_INRACE]:
                                cmt = r[COL_COMMENT]
                            sec.lines.append([cmt, r[COL_BIB], r[COL_NAMESTR]])
                    if len(sec.lines) > 0:
                        if c == '':
                            _log.warning('%d uncategorised riders',
                                         len(sec.lines))
                        ret.append(sec)
                        ret.append(report.pagebreak(threshold=0.1))
                    else:
                        if c:
                            _log.warning('No starters for category %s', c)
        else:
            _log.debug('Preparing flat signon')
            sec = report.signon_list('signon')
            for r in self.riders:
                cmt = None
                if not r[COL_INRACE]:
                    cmt = r[COL_COMMENT]
                sec.lines.append([cmt, r[COL_BIB], r[COL_NAMESTR]])
            ret.append(sec)
        return ret

    def callup_report(self):
        """Return a callup report."""
        # Note: this is just a startlist with different ordering and ranks
        ret = []
        self.reorder_startlist(callup=True)
        if len(self.cats) > 1:
            _log.debug('Preparing categorised callup for %r', self.cats)
            for c in self.cats:
                _log.debug('Callup Cat %s', c)
                ret.extend(self.startlist_report_gen(c, callup=True))
        else:
            _log.debug('Preparing flat callup')
            ret = self.startlist_report_gen(callup=True)
        return ret

    def startlist_report(self):
        """Return a startlist report."""
        ret = []
        self.reorder_startlist()
        if len(self.cats) > 1:
            _log.debug('Preparing categorised startlist for %r', self.cats)
            for c in self.cats:
                if c:
                    _log.debug('Startlist Cat %s', c)
                else:
                    _log.debug('Startlist Uncategorised')
                ret.extend(self.startlist_report_gen(c))
        else:
            _log.debug('Preparing flat startlist')
            ret = self.startlist_report_gen()
        return ret

    def load_cat_data(self):
        """Read category start and target data from riderdb."""
        self.catstarts = {}
        self.catlaps = {}
        onetarget = False
        onemissing = False
        for c in self.cats:
            cs = None  # default start offset is None
            ls = None
            # fetch data on all but the uncat cat
            if c:
                dbr = self.meet.rdb.get_rider(c, 'cat')
                if dbr is not None:
                    ct = tod.mktod(dbr['start offset'])
                    if ct is not None:
                        cs = ct
                    lt = strops.confopt_posint(dbr['target laps'])
                    if lt:
                        ls = lt
                        onetarget = True
                    else:
                        onemissing = True
            self.catstarts[c] = cs
            self.catlaps[c] = ls
        if onetarget:
            if onemissing:
                # There's one or more cats without a target, issue warning
                missing = []
                for c in self.catlaps:
                    if self.catlaps[c] is None:
                        missing.append(repr(c))
                if missing:
                    _log.warning('Categories missing target lap count: %s',
                                 ', '.join(missing))
        _log.debug('Re-load result cats: %r', self.cats)

    def startlist_report_gen(self, cat=None, callup=False):
        catname = ''
        subhead = ''
        footer = ''
        secid = 'startlist'
        uncat = False
        if cat is not None:
            catname = cat
            dbr = self.meet.rdb.get_rider(cat, 'cat')
            if dbr is not None:
                catname = dbr['title']
                subhead = dbr['subtitle']
                footer = dbr['footer']
                secid = 'startlist-' + cat.lower()
            if cat == '':
                catname = 'Uncategorised Riders'
                uncat = True
                secid = 'startlist-uncategorised'
        else:
            cat = ''  # match all riders

        catcache = {'': None}
        if cat == '':
            for c in self.meet.rdb.listcats(self.series):
                if c != '':
                    catnm = c
                    dbr = self.meet.rdb.get_rider(c, 'cat')
                    if dbr is not None:
                        catnm = dbr['title']
                    catcache[c] = catnm

        ret = []
        sec = report.twocol_startlist(secid)
        if callup:
            sec.heading = 'Call-up'
        else:
            sec.heading = 'Startlist'
        if catname:
            sec.heading += ': ' + catname
            sec.subheading = subhead
        rcnt = 0
        # fetch result category for this nominated cat
        cat = self.ridercat(cat)
        for r in self.riders:
            # add rider to startlist if primary cat matches
            cs = r[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if cat == rcat:
                ucicode = None
                name = r[COL_NAMESTR]
                dbr = self.meet.rdb.get_rider(r[COL_BIB], self.series)
                if dbr is not None:
                    ucicode = dbr['uci id']
                comment = ''
                if callup:
                    comment = str(rcnt + 1) + '.'
                if not r[COL_INRACE]:
                    cmt = r[COL_COMMENT]
                    if cmt == 'dns':
                        comment = cmt
                riderno = r[COL_BIB].translate(strops.INTEGER_UTRANS)
                sec.lines.append([comment, riderno, name, ucicode])
                # Look up pilots
                if cat in ('MB', 'WB'):
                    # lookup pilot
                    dbr = self.meet.rdb.get_rider(r[COL_BIB], 'pilot')
                    if dbr is not None:
                        sec.even = True  # force even first column
                        puci = dbr['uci id']
                        pnam = dbr.listname()
                        sec.lines.append(['', '', pnam, puci])
                rcnt += 1
        fvc = []
        if footer:
            fvc.append(footer)
        if rcnt > 1:
            fvc.append('Total riders: ' + str(rcnt))
        if fvc:
            sec.footer = '\t'.join(fvc)
        if cat or len(sec.lines) > 0 or len(self.cats) < 2:
            ret.append(sec)
            if uncat:
                _log.warning('%d uncategorised riders', len(sec.lines))

        return ret

    def analysis_report(self):
        """Return an analysis report."""
        # temporary fall through to camera report
        return self.camera_report()

    def camera_report(self):
        """Return the judges (camera) report."""
        # Note: camera report treats all riders as a single blob
        ret = []
        self.recalculate()  # fill places and bunch info
        pthresh = self.meet._timer.photothresh()
        totcount = 0
        dnscount = 0
        dnfcount = 0
        fincount = 0
        lcomment = ''
        insertgap = True
        if self.timerstat != 'idle':
            sec = report.judgerep('judging')
            sec.heading = 'Judges Report'
            if self.etype == 'cross':
                sec.colheader = [
                    '', 'no', 'rider', 'lap', 'finish', 'lap avg', 'passings'
                ]
            else:
                sec.colheader = [
                    '', 'no', 'rider', 'lap', 'finish', 'rftime', 'passings'
                ]
            if self.start is not None:
                sec.start = self.start
            if self.finish is not None:
                sec.finish = self.maxfinish + tod.tod('0.1')
            sec.laptimes = self.laptimes
            first = True
            ft = None
            lt = None
            lrf = None
            lplaced = None
            ltimed = None
            for r in self.riders:
                totcount += 1
                marker = ' '
                es = ''
                bs = ''
                pset = False
                placed = False
                timed = False
                photo = False
                rbib = r[COL_BIB]
                rcat = r[COL_CAT]
                ecat = self.ridercat(riderdb.primary_cat(rcat))
                catstart = None
                if ecat in self.catstarts and self.etype != 'cross':
                    catstart = self.catstarts[ecat]
                laplist = []
                if r[COL_RFTIME] is not None:
                    for lt in r[COL_RFSEEN]:
                        if lt <= r[COL_RFTIME]:
                            laplist.append(lt)
                else:
                    # include all captured laps
                    laplist = r[COL_RFSEEN]
                if r[COL_INRACE]:
                    comment = str(totcount)
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if bt is not None:
                        timed = True
                        fincount += 1
                        if r[COL_PLACE] != '':
                            comment = r[COL_PLACE] + '.'
                            placed = True
                            pset = True

                        # format 'elapsed' rftime
                        if r[COL_RFTIME] is not None:
                            if not pset and lrf is not None:
                                if r[COL_RFTIME] - lrf < pthresh:
                                    photo = True
                                    if not sec.lines[-1][7]:  # not placed
                                        sec.lines[-1][8] = True
                            if self.start is not None:
                                et = r[COL_RFTIME] - self.start
                                if self.etype == 'cross':
                                    if r[COL_LAPS] > 0:
                                        al = tod.mktod(et.timeval /
                                                       r[COL_LAPS])
                                        es = al.rawtime(1)
                                else:
                                    es = et.rawtime(2)
                            else:
                                es = r[COL_RFTIME].rawtime(2)
                            lrf = r[COL_RFTIME]
                        else:
                            lrf = None

                        # format 'finish' time
                        if ft is None:
                            ft = bt
                            bs = ft.rawtime(0)
                        else:
                            if bt > lt:
                                # New bunch
                                sec.lines.append([None, None, None])
                                down = bt - ft
                                if down < MAXELAP:
                                    bs = '+' + down.rawtime(0)
                            else:
                                # Same time
                                pass
                        lt = bt
                        # sep placed and unplaced
                        insertgap = False
                        if lplaced and placed != lplaced:
                            sec.lines.append([None, None, None])
                            sec.lines.append(
                                [None, None, 'Riders not yet placed'])
                            insertgap = True
                        lplaced = placed
                    else:
                        if r[COL_COMMENT].strip() != '':
                            comment = r[COL_COMMENT].strip()
                        else:
                            comment = '____'

                    # sep timed and untimed
                    if not insertgap and ltimed and ltimed != timed:
                        sec.lines.append([None, None, None])
                        sec.lines.append(
                            [None, None, 'Riders not seen at finish.'])
                        insertgap = True
                    ltimed = timed
                    sec.lines.append([
                        comment, rbib, r[COL_NAMESTR],
                        str(r[COL_LAPS]), bs, es, laplist, placed, photo,
                        catstart, rcat
                    ])
                else:
                    comment = r[COL_COMMENT]
                    if comment == '':
                        comment = 'dnf'
                    if comment != lcomment:
                        sec.lines.append([None, None, None])
                    lcomment = comment
                    if comment == 'dns':
                        dnscount += 1
                    else:
                        dnfcount += 1
                    # format 'elapsed' rftime
                    es = None
                    if r[COL_RFTIME] is not None:  # eg for OTL
                        if self.start is not None:
                            es = (r[COL_RFTIME] - self.start).rawtime(2)
                        else:
                            es = r[COL_RFTIME].rawtime(2)
                    sec.lines.append([
                        comment, rbib, r[COL_NAMESTR],
                        str(r[COL_LAPS]), None, es, laplist, True, False,
                        catstart, rcat
                    ])
                first = False

            ret.append(sec)
            sec = report.section('judgesummary')
            sec.lines.append([None, None, 'Total riders: ' + str(totcount)])
            sec.lines.append([None, None, 'Did not start: ' + str(dnscount)])
            sec.lines.append([None, None, 'Did not finish: ' + str(dnfcount)])
            sec.lines.append([None, None, 'Finishers: ' + str(fincount)])
            residual = totcount - (fincount + dnfcount + dnscount)
            if residual > 0:
                sec.lines.append(
                    [None, None, 'Unaccounted for: ' + str(residual)])
            if len(sec.lines) > 0:
                ret.append(sec)
        else:
            _log.warning('Event is idle, report not available')
        return ret

    def catresult_report(self):
        """Return a categorised event result report."""
        _log.debug('Categorised result report')
        ret = []
        for cat in self.cats:
            ret.extend(self.single_catresult(cat))

        return ret

    def single_catresult(self, cat):
        if cat:
            _log.debug('Result Cat %s', cat)
        else:
            _log.debug('Result Uncategorised')
        ret = []
        allin = False
        catname = cat
        secid = 'result'
        if cat == '':
            if len(self.cats) > 1:
                catname = 'Uncategorised Riders'
            else:
                # There is only one cat - so all riders are in it
                allin = True
        else:
            secid = 'result-' + cat.lower()
        subhead = ''
        footer = ''
        distance = self.meet.get_distance()
        laps = self.totlaps
        if cat in self.catlaps and self.catlaps[cat] is not None:
            laps = self.catlaps[cat]
        doflap = self.dofastestlap
        if self.start is None:
            doflap = False  # don't do laps unless start is set
        flap = None
        fno = None
        fcnt = None
        dbr = self.meet.rdb.get_rider(cat, 'cat')
        if dbr is not None:
            catname = dbr['title']
            subhead = dbr['subtitle']
            footer = dbr['footer']
            dist = dbr['distance']
            if dist:
                try:
                    distance = float(dist)
                except Exception:
                    _log.warning('Invalid distance %r for cat %r', dist, cat)
        sec = report.section(secid)

        wt = None
        bwt = None
        leadpass = None
        leadlap = None
        leadsplits = None
        lt = None
        first = True
        lcomment = ''
        lp = None
        lsrc = None
        rcnt = 0
        plcnt = 1
        jcnt = 0
        vcnt = 0
        totcount = 0
        dnscount = 0
        dnfcount = 0
        hdcount = 0
        fincount = 0
        for r in self.riders:
            rcat = r[COL_CAT].upper()
            rcats = ['']
            if rcat.strip():
                rcats = rcat.split()
            incat = False
            if allin or (cat and cat in rcats):
                incat = True  # rider is in this category
            elif not cat:  # is the rider uncategorised?
                if rcats[0] == '':
                    incat = True
                else:
                    # exclude properly categorised riders
                    incat = rcats[0] not in self.cats
            if incat:
                if cat:
                    rcat = cat
                else:
                    rcat = rcats[0]  # (work-around mis-categorised rider)
                totcount += 1
                sof = None  # all riders have a start time offset
                if r[COL_STOFT] is not None:
                    sof = r[COL_STOFT]
                elif rcat in self.catstarts:
                    sof = self.catstarts[rcat]
                bstr = r[COL_BIB]
                nstr = r[COL_NAMESTR]
                rlap = r[COL_LAPS]
                pstr = ''
                tstr = ''  # cross laps down
                dstr = ''  # time/gap
                cstr = ''
                rpass = None
                dbr = self.meet.rdb.get_rider(bstr, self.series)
                if dbr is not None:
                    cstr = dbr['uci id']
                placed = False  # placed at finish
                timed = False  # timed at finish
                virtual = False  # oncourse
                comment = None
                if r[COL_INRACE]:
                    psrc = r[COL_PLACE]
                    if psrc != '':
                        placed = True
                        if lsrc != psrc:  # previous total place differs
                            lp = str(plcnt)
                        else:
                            pass  # dead heat in cat
                        lsrc = psrc
                    else:
                        lp = ''
                    plcnt += 1
                    pstr = ''
                    if lp is not None and lp != '':
                        pstr = lp + '.'
                        jcnt += 1
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if self.etype == 'cross':
                        ronlap = True
                        risleader = False
                        dtlap = rlap
                        if leadpass is None and rlap > 0:
                            risleader = True
                            leadlap = rlap
                            if len(r[COL_RFSEEN]) > 0:
                                # an untimed leader with manual lap count
                                leadpass = r[COL_RFSEEN][-1]
                                leadsplits = [tv for tv in r[COL_RFSEEN]]
                        if rlap > 0 and leadpass is not None:
                            if len(r[COL_RFSEEN]) > 0:
                                rpass = r[COL_RFSEEN][-1]
                            if bt is None:
                                if rpass is not None and rpass < leadpass:
                                    # rider is still finishing a lap
                                    rlap += 1
                                    ronlap = False
                                virtual = True
                                vcnt += 1
                                dstr = ''
                        if leadlap is not None:
                            if leadlap != rlap and rlap > 0:
                                # show laps down in time column
                                virtual = True
                                tstr = '-{0:d} lap{1}'.format(
                                    leadlap - rlap,
                                    strops.plural(leadlap - rlap))
                                # invalidate bunch times for this rider
                                bwt = None
                                bt = None
                        if risleader and self.start is not None:
                            if leadpass is not None:
                                et = leadpass - self.start
                                if sof is not None:
                                    et = et - sof
                                dstr = et.rawtime(0)
                        elif bt is None and self.showdowntimes:
                            # synthesise down time if possible
                            if dtlap > 0:
                                rlpass = None
                                if len(r[COL_RFSEEN]) >= dtlap:
                                    rlpass = r[COL_RFSEEN][dtlap - 1]
                                llpass = None
                                if len(leadsplits) >= dtlap:
                                    llpass = leadsplits[dtlap - 1]
                                else:
                                    _log.debug('Lap down time not available')
                                if llpass is not None and rlpass is not None:
                                    rdown = rlpass - llpass
                                    if rdown < MAXELAP:
                                        dstr = '+' + rdown.rawtime(0)
                                        if not ronlap:
                                            dstr = '[' + dstr + ']'
                                    else:
                                        # probably a change of leader
                                        if rpass is not None:
                                            et = rpass - self.start
                                            if sof is not None:
                                                et = et - sof
                                            dstr = '[' + et.rawtime(0) + ']'

                    if bt is not None:
                        fincount += 1  # for accounting, use bunch time
                        timed = True
                        # compute elapsed
                        et = bt
                        if sof is not None:
                            # apply a start offset
                            et = bt - sof
                        if wt is None:  # first finish time
                            wt = et
                            if rlap != laps:
                                # assume the distance is invalid
                                distance = None
                        if bwt is not None:
                            if self.showdowntimes:
                                down = et - bwt
                                if down < MAXELAP:
                                    dstr = '+' + down.rawtime(0)
                        else:
                            dstr = et.rawtime(0)
                        first = False
                        if bwt is None:
                            bwt = et
                    lt = bt
                else:
                    # Non-finishers dns, dnf, otl, dsq
                    placed = True  # for purpose of listing
                    comment = r[COL_COMMENT]
                    if comment == '':
                        comment = 'dnf'
                    if comment != lcomment:
                        sec.lines.append([None, None, None])  # new bunch
                    lcomment = comment
                    # account for special cases
                    if comment == 'dns':
                        dnscount += 1
                    elif comment == 'otl':
                        # otl special case: also show down time if possible
                        bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                        if bt is not None and self.showdowntimes:
                            if not first and wt is not None:
                                et = bt
                                if sof is not None:
                                    # apply a start offset
                                    et = bt - sof
                                down = et - wt
                                if down < MAXELAP:
                                    dstr = '+' + down.rawtime(0)
                        hdcount += 1
                    else:
                        dnfcount += 1
                    pstr = comment
                if placed or timed or virtual:
                    sec.lines.append([pstr, bstr, nstr, cstr, tstr, dstr])
                    ## and look up pilots?
                    if cat in ('MB', 'WB'):
                        sec.even = True  # twocol result
                        # lookup pilot
                        dbr = self.meet.rdb.get_rider(bstr, 'pilot')
                        if dbr is not None:
                            puci = dbr['uci id']
                            pnam = dbr.listname()
                            sec.lines.append(['', 'pilot', pnam, puci, '', ''])
                if doflap and comment != 'dns':
                    if len(r[COL_RFSEEN]) > 0:
                        # only consider laps between stime and ftime
                        stime = self.start
                        if sof is not None:
                            stime += sof
                        ftime = tod.now()
                        if r[COL_RFTIME] is not None:
                            ftime = r[COL_RFTIME]
                        ls = stime
                        lt = None
                        lc = 0
                        for p in r[COL_RFSEEN]:
                            if ls >= ftime:
                                break  # lap starts after end of region
                            if p < ls:
                                continue  # passing before start of region
                            else:
                                lt = p - ls
                                if lt > self.minlap:
                                    lc += 1  # consider this a legit lap
                                    if flap is None or lt < flap:  # new fastest
                                        flap = lt
                                        fno = bstr
                                        fcnt = lc
                                else:
                                    pass
                                    # short lap
                                ls = p
                rcnt += 1
            else:
                # not in this category.
                pass
        if self.timerstat in ('idle', 'finished'):
            sec.heading = 'Result'
        elif self.timerstat in ('armstart', 'running', 'armfinish'):
            # set status if number of judged riders greater than jtgt
            jtgt = 10
            javail = totcount - (dnfcount + dnscount + hdcount)
            if javail < 10:
                jtgt = min(3, javail)
            if javail > 0 and jcnt >= jtgt:
                sec.heading = 'Provisional Result'
            elif vcnt > 0:
                sec.heading = 'Virtual Standing'
            else:
                sec.heading = 'Race In Progress'
        else:
            sec.heading = 'Provisional Result'
        if footer:
            sec.footer = footer

        # Append all result categories and uncat if appropriate
        if cat or totcount > 0 or len(self.cats) < 2:
            ret.append(sec)
            rsec = sec
            # Race metadata / UCI comments
            secid = 'resultmeta'
            if cat:
                secid = 'resultmeta-' + cat.lower()
            sec = report.bullet_text(secid)
            if wt is not None:
                if distance is not None:
                    sec.lines.append([
                        None, 'Average speed of the winner: ' +
                        wt.speedstr(1000.0 * distance)
                    ])
            if doflap and fno is not None:
                ftr = self.getrider(fno)
                fts = ''
                if ftr is not None:
                    fts = ftr[COL_SHORTNAME]
                ftstr = flap.rawtime(0)
                if flap < tod.tod(60):
                    ftstr += ' sec'
                fmsg = 'Fastest lap: {} {} {} on lap {:d}'.format(
                    fno, fts, ftstr, fcnt)
                sec.lines.append([None, fmsg])

            sec.lines.append(
                [None, 'Number of starters: ' + str(totcount - dnscount)])
            if hdcount > 0:
                sec.lines.append([
                    None,
                    'Riders finishing out of time limits: ' + str(hdcount)
                ])
            if dnfcount > 0:
                sec.lines.append(
                    [None, 'Riders abandoning the event: ' + str(dnfcount)])
            residual = totcount - (fincount + dnfcount + dnscount + hdcount)
            if residual > 0:
                if cat:
                    _log.info('Cat %s unaccounted for: %d', cat, residual)
                else:
                    _log.info('Riders unaccounted for: %d', residual)
            ret.append(sec)

            # finish report title manipulation
            if catname:
                cv = []
                if rsec.heading:
                    cv.append(rsec.heading)
                cv.append(catname)
                rsec.heading = ': '.join(cv)
                rsec.subheading = subhead
            ret.append(report.pagebreak())
        return ret

    def result_report(self):
        """Return a result report."""
        ret = []
        self.recalculate()

        # check if a categorised report is required
        if self.etype == 'handicap':
            ret.extend(self.handicap_report())
            if self.hcpcatres:
                ret.extend(self.catresult_report())
        else:
            ret.extend(self.catresult_report())

        # show all intermediates here
        if self.intermeds:
            catcache = {'': None}
            for c in self.meet.rdb.listcats(self.series):
                if c != '':
                    catnm = c
                    dbr = self.meet.rdb.get_rider(c, 'cat')
                    if dbr is not None:
                        catnm = dbr['title']
                    catcache[c] = catnm
            for i in self.intermeds:
                im = self.intermap[i]
                if im['places'] and im['show']:
                    ret.extend(self.int_report(i, catcache))

        # append a decisions section
        ret.append(self.decision_section())

        return ret

    def decision_section(self):
        """Return an officials decision section"""
        ret = report.bullet_text('decisions')
        if self.decisions:
            ret.heading = 'Decisions of the commissaires panel'
            for decision in self.decisions:
                if decision:
                    ret.lines.append((None, self.decision_format(decision)))
        return ret

    def decision_format(self, decision):
        """Crudely macro format a commissaire decision string"""
        ret = []
        for line in decision.split('\n'):
            if line:
                ol = []
                for word in line.split():
                    if word.startswith('r:'):
                        punc = ''
                        if not word[-1].isalnum():
                            punc = word[-1]
                            word = word[0:-1]
                        rep = word
                        look = word.split(':', 1)[-1]
                        _log.debug('Look up rider: %s', look)
                        rid = self.meet.rdb.get_id(look)
                        if rid is not None:
                            rep = self.meet.rdb[rid].name_bib()
                        ol.append(rep + punc)
                    elif word.startswith('t:'):
                        punc = ''
                        if not word[-1].isalnum():
                            punc = word[-1]
                            word = word[0:-1]
                        rep = word
                        look = word.split(':', 1)[-1]
                        _log.debug('Look up team: %s', look)
                        rid = self.meet.rdb.get_id(look, 'team')
                        if rid is not None:
                            rep = self.meet.rdb[rid][
                                'first'] + ' (' + look.upper() + ')'
                        ol.append(rep + punc)
                    elif word.startswith('d:'):
                        punc = ''
                        if not word[-1].isalnum():
                            punc = word[-1]
                            word = word[0:-1]
                        rep = word
                        look = word.split(':', 1)[-1]
                        _log.debug('Look up ds: %s', look)
                        rid = self.meet.rdb.get_id(look, 'ds')
                        if rid is not None:
                            rep = self.meet.rdb[rid].fitname(48)
                        ol.append(rep + punc)
                    else:
                        ol.append(word)
                ret.append(' '.join(ol))
        return '\n'.join(ret)

    def handicap_report(self):
        _log.debug('Result report in handicap path')
        ret = []
        wt = None
        we = None
        dofastest = False  # ftime for handicaps
        fastest = None
        vfastest = None
        curelap = None
        if self.start is not None:  # virtual bunch time
            curelap = (tod.now() - self.start).truncate(0)
        fastestbib = None
        totcount = 0
        dnscount = 0
        dnfcount = 0
        hdcount = 0
        fincount = 0
        lcomment = ''
        gapcount = 0
        catcache = {'': None}
        for c in self.meet.rdb.listcats(self.series):
            if c != '':
                catnm = c
                dbr = self.meet.rdb.get_rider(c, 'cat')
                if dbr is not None:
                    catnm = dbr['title']
                catcache[c] = catnm
        lt = None
        sec = report.section('result')
        sec.colheader = [None, None, None, None, 'Elapsed', 'Time/Gap']
        if self.racestat in ('prerace', 'final'):
            sec.heading = 'Result'
        elif self.racestat == 'provisional':
            sec.heading = 'Provisional Result'
        else:
            sec.heading = 'Race In Progress'

        first = True
        for r in self.riders:
            totcount += 1
            bstr = r[COL_BIB]  # 'bib'
            nstr = r[COL_NAMESTR]  # 'name'
            # in handicap - only primary category is considered
            cs = r[COL_CAT]
            cstr = riderdb.primary_cat(cs)  # 'cat'
            rcat = self.ridercat(cstr)
            # in handicap result, cat overrides UCIID
            if cstr.upper() in catcache:
                cstr = catcache[cstr.upper()]
            pstr = ''  # 'place'
            tstr = ''  # 'elap' (hcp only)
            dstr = ''  # 'time/gap'
            placed = False  # placed at finish
            timed = False  # timed at finish
            if r[COL_INRACE]:
                psrc = r[COL_PLACE]
                if psrc != '':
                    pstr = psrc + '.'
                    placed = True
                bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                if bt is not None:
                    timed = True
                    fincount += 1  # for accounting, use bunch time
                    if wt is None:  # first finish time
                        wt = bt
                        first = False
                        # for hcap, first time is always event time
                        dstr = wt.rawtime(0)
                    else:
                        # for handicap, time gap is always
                        # down on winner's uncorrected time
                        if self.showdowntimes:
                            down = bt - wt
                            if down < MAXELAP:
                                dstr = '+' + down.rawtime(0)

                    # show elapsed for hcp ...*
                    tstr = bt.rawtime(0)
                    et = bt
                    sof = None
                    if r[COL_STOFT] is not None:  # apply a start offset
                        sof = r[COL_STOFT]
                    elif rcat in self.catstarts:
                        sof = self.catstarts[rcat]
                    if sof is not None:
                        dofastest = True  # will need to report!
                        et = bt - sof
                        # *... adjust if a start offset is present
                        tstr = et.rawtime(0)
                        if we is None:
                            we = et
                    if fastest is None or et < fastest:
                        fastest = et
                        fastestbib = r[COL_BIB]
                else:  # check virtual finish time
                    sof = None
                    if r[COL_STOFT] is not None:
                        sof = r[COL_STOFT]
                    elif rcat in self.catstarts and self.catstarts[
                            rcat] != tod.ZERO:
                        sof = self.catstarts[rcat]
                    if sof is not None and curelap is not None:
                        vt = curelap - sof
                        if vfastest is None or vt < vfastest:
                            vfastest = vt
                lt = bt
            else:
                # Non-finishers dns, dnf, otl, dsq
                placed = True  # for purpose of listing
                comment = r[COL_COMMENT]
                if comment == '':
                    comment = 'dnf'
                if comment != lcomment:
                    sec.lines.append([None, None, None])  # new bunch
                lcomment = comment
                # account for special cases
                if comment == 'dns':
                    dnscount += 1
                elif comment == 'otl':
                    # otl special case: also show down time if possible
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if bt is not None:
                        if not first and wt is not None:
                            et = bt
                            sof = None
                            if r[COL_STOFT] is not None:
                                sof = r[COL_STOFT]
                            elif rcat in self.catstarts:
                                sof = self.catstarts[rcat]
                            if sof is not None:
                                # apply a start offset
                                et = bt - sof
                            down = et - wt
                            if down < MAXELAP:
                                dstr = '+' + down.rawtime(0)
                    hdcount += 1
                else:
                    dnfcount += 1
                pstr = comment
            if placed or timed:
                sec.lines.append([pstr, bstr, nstr, cstr, tstr, dstr])
        ret.append(sec)

        # Race metadata / UCI comments
        sec = report.bullet_text('resultmeta')
        if wt is not None:
            sec.lines.append([None, 'Race time: ' + wt.rawtime(0)])
            if we is None:
                we = wt
            dval = self.meet.get_distance()
            if dval is not None:
                sec.lines.append([
                    None, 'Average speed of the winner: ' +
                    we.speedstr(1000.0 * dval)
                ])
        if dofastest:
            if vfastest and vfastest < fastest:
                _log.info('Fastest time not yet available')
            else:
                ftr = self.getrider(fastestbib)
                fts = ''
                if ftr is not None:
                    fts = ftr[COL_SHORTNAME]
                fmsg = ('Fastest time: ' + fastest.rawtime(0) + '  ' +
                        fastestbib + ' ' + fts)
                smsg = ('Fastest time - ' + fts + ' ' + fastest.rawtime(0))
                sec.lines.append([None, fmsg])
                if not self.readonly:  # in a ui window?
                    self.meet.cmd_announce('resultmsg', fmsg)
                    self.meet.cmd_announce('scrollmsg', smsg)

        sec.lines.append(
            [None, 'Number of starters: ' + str(totcount - dnscount)])
        if hdcount > 0:
            sec.lines.append(
                [None, 'Riders finishing out of time limits: ' + str(hdcount)])
        if dnfcount > 0:
            sec.lines.append(
                [None, 'Riders abandoning the event: ' + str(dnfcount)])
        residual = totcount - (fincount + dnfcount + dnscount + hdcount)
        if residual > 0:
            _log.info('%s unaccounted for', residual)
        ret.append(sec)

        return ret

    def stat_but_clicked(self, button=None):
        """Deal with a status button click in the main container."""
        _log.debug('Stat button clicked')

    def ctrl_change(self, acode='', entry=None):
        """Notify change in action combo."""
        if acode == 'fin':
            if entry is not None:
                entry.set_text(self.places)
        elif acode in self.intermeds:
            if entry is not None:
                entry.set_text(self.intermap[acode]['places'])
        else:
            if entry is not None:
                entry.set_text('')

    def event_ctrl_add(self, rlist):
        """Add the supplied riders to event model with lookup"""
        rlist = strops.riderlist_split(rlist, self.meet.rdb, self.series)
        for bib in rlist:
            self.addrider(bib)
        return True

    def event_ctrl_del(self, rlist):
        """Delete nominated riders from event model"""
        rlist = strops.riderlist_split(rlist, self.meet.rdb, self.series)
        for bib in rlist:
            self.delrider(bib)
        return True

    def event_ctrl(self, acode='', rlist=''):
        """Apply the selected action to the provided bib list."""
        if acode in self.intermeds:
            if acode == 'brk':
                rlist = ' '.join(strops.riderlist_split(rlist))
                self.intsprint(acode, rlist)
            else:
                rlist = strops.reformat_placelist(rlist)
                if self.checkplaces(rlist, dnf=False):
                    self.intermap[acode]['places'] = rlist
                    self.recalculate()
                    self.intsprint(acode, rlist)
                    _log.info('Intermediate %s == %r', acode, rlist)
                else:
                    _log.error('Intermediate %s not updated', acode)
            return False
        elif acode == 'fin':
            rlist = strops.reformat_placelist(rlist)
            if self.checkplaces(rlist):
                self.places = rlist
                self.recalculate()
                self.finsprint(rlist)
                return False
            else:
                _log.error('Places not updated')
                return False
        elif acode == 'dnf':
            self.dnfriders(strops.reformat_biblist(rlist))
            return True
        elif acode == 'dsq':
            self.dnfriders(strops.reformat_biblist(rlist), 'dsq')
            return True
        elif acode == 'otl':
            self.dnfriders(strops.reformat_biblist(rlist), 'otl')
            return True
        elif acode == 'wd':
            self.dnfriders(strops.reformat_biblist(rlist), 'wd')
            return True
        elif acode == 'dns':
            self.dnfriders(strops.reformat_biblist(rlist), 'dns')
            return True
        elif acode == 'ret':
            self.retriders(strops.reformat_biblist(rlist))
            return True
        elif acode == 'man':
            # crude hack tool for now
            self.manpassing(strops.reformat_bibserlist(rlist))
            return True
        elif acode == 'del':
            return self.event_ctrl_del(rlist)
        elif acode == 'add':
            return self.event_ctrl_add(rlist)
        elif acode == 'que':
            rlist = strops.reformat_biblist(rlist)
            if rlist != '':
                for bib in rlist.split():
                    self.query_rider(bib)
            return True
        elif acode == 'dec':
            self.add_decision(rlist)
            return True
        else:
            _log.error('Ignoring invalid action %r', acode)
        return False

    def add_decision(self, decision=''):
        """Append a decision of the commissaires panel."""
        self.decisions.append(decision.strip())
        _log.debug('Added decision: %r', decision)

    def query_rider(self, bib=None):
        """List info on selected rider in the scratchpad."""
        _log.info('Query rider: %s', bib)
        r = self.getrider(bib)
        if r is not None:
            ns = strops.truncpad(r[COL_NAMESTR] + ' ' + r[COL_CAT], 30)
            bs = ''
            bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
            if bt is not None:
                bs = bt.timestr(0)
            ps = r[COL_COMMENT]
            if r[COL_PLACE] != '':
                ps = strops.rank2ord(r[COL_PLACE])
            _log.info('%s %s %s %s', bib, ns, bs, ps)
            lt = None
            if len(r[COL_RFSEEN]) > 0:
                for rft in r[COL_RFSEEN]:
                    nt = rft.truncate(0)
                    ns = rft.timestr(1)
                    ls = ''
                    if lt is not None:
                        ls = (nt - lt).timestr(0)
                    _log.info('\t%s %s', ns, ls)
                    lt = nt
            if r[COL_RFTIME] is not None:
                _log.info('Finish: %s', r[COL_RFTIME].timestr(1))
        else:
            _log.info('Rider %s not in startlist', bib)

    def startlist_gen(self, cat=''):
        """Generator function to export a startlist."""
        mcat = self.ridercat(cat)
        self.reorder_startlist()
        for r in self.riders:
            cs = r[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if mcat == rcat:
                start = ''
                if r[COL_STOFT] is not None and r[COL_STOFT] != tod.ZERO:
                    start = r[COL_STOFT].rawtime(0)
                elif rcat in self.catstarts:
                    if self.catstarts[rcat] is not None:
                        start = self.catstarts[rcat].rawtime(0)
                bib = r[COL_BIB]
                series = self.series
                name = r[COL_NAMESTR]
                cat = rcat
                firstxtra = ''
                lastxtra = ''
                clubxtra = ''
                dbr = self.meet.rdb.get_rider(bib, self.series)
                if dbr is not None:
                    firstxtra = dbr['first'].capitalize()
                    lastxtra = dbr['last'].upper()
                    clubxtra = dbr['org']
                yield [
                    start, bib, series, name, cat, firstxtra, lastxtra,
                    clubxtra
                ]

    def lifexport(self):
        """Export lif."""
        self.recalculate()
        st = tod.ZERO
        if self.start is not None:
            st = self.start
        sno = '1'
        if self.meet.mirrorpath:
            sno = self.meet.mirrorfile
        rdx = 1
        odat = [[
            sno, '1', '1', self.meet.subtitle, '', '', '', '', '', '', '', ''
        ]]

        for r in self.riders:
            bib = r[COL_BIB]
            if r[COL_INRACE]:
                if r[COL_RFTIME]:
                    last = ''
                    first = ''
                    team = ''
                    ucicode = ''
                    dbr = self.meet.rdb.get_rider(bib, self.series)
                    if dbr is not None:
                        first = dbr['first'].capitalize()
                        last = dbr['last'].upper()
                        team = dbr['org']
                        ucicode = dbr['uci id']
                    rftime = '0'
                    if r[COL_RFTIME] is not None:
                        rftime = (r[COL_RFTIME] - st).rawtime(2, hoursep=':')
                    bunch = ''
                    if r[COL_CBUNCH] is not None:
                        bunch = r[COL_CBUNCH].rawtime(0, hoursep=':')
                    # rider with time
                    odat.append([
                        str(rdx), bib, bib, last, first, team, rftime, ucicode,
                        bunch, '', '', ''
                    ])
                    rdx += 1
        return odat

    def result_gen(self, cat=''):
        """Create and return result list with rows:

           ( rank, bib, time, bonus, penalty )

        Notes: 

           - Handicap event type includes start offset in bonus time
           - Cross type adjusts time to include cat leader's average
             lap time and time down at finish
        """
        self.recalculate()
        mcat = self.ridercat(cat)
        rcount = 0
        lrank = None
        lcrank = None
        lft = None
        lavg = None
        lbib = None
        llaps = None
        lpass = None
        ret = []
        for r in self.riders:
            rcat = r[COL_CAT].upper()
            rcats = ['']
            if rcat.strip():
                rcats = rcat.split()
            if mcat == '' or mcat in rcats:
                if mcat:
                    rcat = mcat
                else:
                    rcat = rcats[0]
                rcount += 1
                bib = r[COL_BIB]
                crank = None
                rank = None
                bonus = None
                ft = None
                if r[COL_INRACE]:
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    ft = bt
                    sof = None
                    if r[COL_STOFT] is not None:
                        sof = r[COL_STOFT]
                    elif rcat in self.catstarts:
                        sof = self.catstarts[rcat]
                    if sof is not None and bt is not None:
                        if self.etype == 'cross':
                            if lavg is None:
                                llaps = r[COL_LAPS]
                                lpass = r[COL_RFSEEN]
                                lavg = tod.tod(ft.timeval / llaps)
                                lbib = bib
                                ft = bt - sof
                                lft = ft
                                _log.debug(
                                    'Leader %s: %d laps, lap avg: %s, ft: %s',
                                    bib, llaps, lavg.rawtime(6), ft.rawtime(0))
                            else:
                                # do the faux down time
                                lxtra = tod.ZERO
                                rcnt = r[COL_LAPS]
                                rdwn = llaps - rcnt
                                if rcnt != llaps:
                                    lelap = lft
                                    lxtra = tod.tod(lavg.timeval * rdwn)
                                    if bt < ft:
                                        # is this a valid finish time?
                                        _log.error(
                                            '%s finish time %s ahead of cat leader %s: %s',
                                            bib, ft.rawtime(0), lbib,
                                            lft.rawtime(0))
                                ft = bt + lxtra - sof
                        elif self.etype == 'handicap':
                            # for handicap, time is stage time, bonus
                            # carries the start offset, elapsed is:
                            # stage - bonus
                            ft = bt
                            bonus = sof
                        else:
                            ft = bt - sof
                plstr = r[COL_PLACE]
                if plstr.isdigit():
                    rank = int(plstr)
                    if rank != lrank:
                        crank = rcount
                    else:
                        crank = lcrank
                    lcrank = crank
                    lrank = rank
                else:
                    crank = r[COL_COMMENT]
                if self.etype != 'handicap' and (bib in self.bonuses
                                                 or r[COL_BONUS] is not None):
                    bonus = tod.ZERO
                    if bib in self.bonuses:
                        bonus += self.bonuses[bib]
                    if r[COL_BONUS] is not None:
                        bonus += r[COL_BONUS]
                penalty = None
                if r[COL_PENALTY] is not None:
                    penalty = r[COL_PENALTY]
                if ft is not None:
                    ft = ft.truncate(0)  # force whole second for bunch times
                ret.append((crank, bib, ft, bonus, penalty))
        return ret

    def clear_results(self):
        """Clear all data from event model."""
        self.resetplaces()
        self.places = ''
        _log.debug('Clear event result')
        # scan riders to clear any event info
        for r in self.riders:
            r[COL_COMMENT] = ''
            r[COL_INRACE] = True
            r[COL_PLACE] = ''
            r[COL_LAPS] = 0
            r[COL_RFSEEN] = []
            r[COL_RFTIME] = None
            r[COL_CBUNCH] = None
            r[COL_MBUNCH] = None
        _log.debug('Clear rider data')

    def getrider(self, bib, series=''):
        """Return reference to selected rider no."""
        ret = None
        if series == self.series:
            for r in self.riders:
                if r[COL_BIB] == bib:
                    ret = r
                    break
        return ret

    def getiter(self, bib, series=''):
        """Return temporary iterator to model row."""
        i = None
        if series == self.series:
            i = self.riders.get_iter_first()
            while i is not None:
                if self.riders.get_value(i, COL_BIB) == bib:
                    break
                i = self.riders.iter_next(i)
        return i

    def delrider(self, bib='', series=''):
        """Remove the specified rider from the model."""
        if series == self.series:
            self.clear_place(bib)
            i = self.getiter(bib, series)
            if i is not None:
                self.riders.remove(i)
                self.ridernos.remove(bib)

    def starttime(self, start=None, bib='', series=''):
        """Adjust start time for the rider."""
        if series == self.series:
            r = self.getrider(bib)
            if r is not None:
                r[COL_STOFT] = start

    def addrider(self, bib='', series=None):
        """Add specified rider to event model, return tree iter."""
        if series is not None and series != self.series:
            _log.debug('Ignoring non-series rider: %s',
                       strops.bibser2bibstr(bib, series))
            return None

        if bib and bib in self.ridernos:
            _log.info('Rider %s already in viewmodel', bib)
            return None

        if bib:
            nr = [
                bib, '', '', '', '', True, '', 0, 0, None, None, None, None,
                None, None, []
            ]
            dbr = self.meet.rdb.get_rider(bib, self.series)
            if dbr is None:
                self.meet.rdb.add_empty(bib, self.series)
                dbr = self.meet.rdb.get_rider(bib, self.series)
            if dbr is not None:
                self.updaterider(nr, dbr)
            self.ridernos.add(bib)
            return self.riders.append(nr)
        else:
            return None

    def updaterider(self, lr, r):
        """Update the local record lr with data from riderdb handle r"""
        lr[COL_NAMESTR] = r.listname()
        lr[COL_SHORTNAME] = r.fitname(24)
        lr[COL_CAT] = r['cat']
        if lr[COL_SEED] == 0:
            # Import seed from notes column if int
            seed = strops.confopt_posint(r['notes'])
            if seed is not None:
                lr[COL_SEED] = seed

    def resettimer(self):
        """Reset event timer."""
        _log.debug('Clear event timers')
        self.set_finish()
        self.set_start()
        self.lapstart = None
        self.lapfin = None
        self.clear_results()
        self.timerstat = 'idle'
        self.racestat = 'prerace'
        self.meet.cmd_announce('timerstat', 'idle')
        self.meet.stat_but.update('idle', 'Idle')
        self.meet.stat_but.set_sensitive(True)
        self.curlap = -1
        self.onlap = 1
        self.resetcatonlaps()
        self.lapentry.set_text('')
        self.laptimes = []
        self.live_announce = True

    def armstart(self):
        """Process an armstart request."""
        if self.timerstat == 'idle':
            self.timerstat = 'armstart'
            self.meet.cmd_announce('timerstat', 'armstart')
            self.meet.stat_but.update('activity', 'Arm Start')
            self.resetcatonlaps()
            self.armlap()
            if self.live_announce:
                self.meet.cmd_announce('clear', 'all')
                if self.etype in ('criterium', 'circuit', 'cross'):
                    self.armlap()

        elif self.timerstat == 'armstart':
            self.timerstat = 'idle'
            self.meet.cmd_announce('timerstat', 'idle')
            self.meet.stat_but.update('idle', 'Idle')

    def resetcatonlaps(self):
        onechange = False
        for cat in self.catlaps:  # for each category with a defined target
            self.catonlap[cat] = 0
            target = self.catlaps[cat]
            if target:
                onechange = True
        if onechange:
            self.announcecatlap()

    def armfinish(self):
        """Process an armfinish request."""
        if self.timerstat in ('running', 'finished'):
            if self.finish is None and self.curlap:
                # No finish passing yet
                self.armlap()
            elif self.totlaps == 0:
                # Unbound lap count
                self.armlap()
            self.timerstat = 'armfinish'
            self.meet.cmd_announce('timerstat', 'armfinish')
            self.meet.stat_but.update('error', 'Arm Finish')
            self.meet.stat_but.set_sensitive(True)
        elif self.timerstat == 'armfinish':
            self.timerstat = 'running'
            self.meet.cmd_announce('timerstat', 'running')
            self.meet.stat_but.update('ok', 'Running')

    def last_rftime(self):
        """Find the last rider with a RFID finish time set."""
        ret = None
        for r in self.riders:
            if r[COL_RFTIME] is not None:
                ret = r[COL_BIB]
        return ret

    def armlap(self, data=None):
        _log.debug('Arm lap')
        if self.curlap is None or self.curlap < 0:
            self.curlap = 0  # manual override lap counts
        self.scratch_map = {}
        self.scratch_ord = []
        titlestr = self.title_namestr.get_text()
        if self.live_announce:
            self.meet.cmd_announce('clear', 'all')
        if self.timerstat in ('idle', 'armstart', 'armfinish'):
            self.meet.cmd_announce('finstr', self.meet.get_short_name())
            if self.timerstat in ('idle', 'armstart'):
                self.reannounce_times()  # otherwise not called
                self.meet.cmd_announce('title', titlestr)  # enforce
                return False  # no arm till event underway
        if self.curlap <= 0 or self.lapfin is not None:
            self.curlap += 1  # increment

            if self.totlaps and self.curlap > self.totlaps:
                _log.info('Too many laps')
                self.curlap = self.totlaps

            # sanity check onlap
            # once arm lap is done, curlap and onlap _should_ be same
            if self.onlap != self.curlap:
                _log.debug('Lap mismatch: curlap=%d onlap=%d', self.curlap,
                           self.onlap)
                if self.curlap == 1:
                    # assume this is an in-race correction
                    self.curlap = self.onlap
                    _log.debug('Curlap set to %d from onlap', self.curlap)
                else:
                    # assume the curlap is set to the desired count
                    self.onlap = self.curlap
                    _log.debug('Onlap set to %d from curlap', self.curlap)
                self.meet.cmd_announce('onlap', str(self.onlap))

        # update curlap entry whatever happened
        self.lapentry.set_text(str(self.curlap))

        # write lap time fields
        lapstr = None
        if self.timerstat not in ('armfinish', 'finished'):
            self.meet.cmd_announce('bunches', 'laps')
            self.meet.cmd_announce('finstr', None)
            ## Step 1: lap time handling
            if self.lapfin:
                # roll over to lap start
                self.lapstart = self.lapfin
            elif self.lapstart:  # assume still waiting for same lap
                pass
            else:  # at start?
                self.lapstart = self.start
            if self.onlap is not None:
                if self.totlaps is not None and self.totlaps > 0:
                    lapstr = ('Lap ' + str(self.onlap) + '/' +
                              str(self.totlaps))
                    self.totlapentry.set_text(str(self.totlaps))
                else:  # 0 flags unknown total
                    lapstr = ''
                    passkey = str(self.curlap)
                    if passkey in self.passlabels:
                        lapstr = 'At ' + self.passlabels[passkey]
                    else:
                        if self.etype in ('circuit', 'criterium', 'cross'):
                            lapstr = 'Lap ' + str(self.onlap)
                self.meet.cmd_announce('laplbl', lapstr)
            else:
                # make sure something is displayed in this path
                self.meet.cmd_announce('laplbl', None)
                self.meet.cmd_announce('finstr', self.meet.get_short_name())
            self.lapfin = None
        else:
            self.meet.cmd_announce('bunches', 'final')
        self.meet.cmd_announce('title', titlestr)
        self.reannounce_times()
        # in case of idle/delay
        return False

    def key_event(self, widget, event):
        """Handle global key presses in event."""
        if event.type == Gdk.EventType.KEY_PRESS:
            key = Gdk.keyval_name(event.keyval) or 'None'
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                if key == key_announce:  # re-send current announce vars
                    self.reannounce_times()
                    return True
                elif key == key_clearfrom:  # clear all places from selected
                    self.clear_places_from_selection()
                    return True
                elif key == key_clearplace:  # clear selected rider from places
                    self.clear_selected_place()
                    return True
            if key[0] == 'F':
                if key == key_announce:
                    if self.places:
                        self.finsprint(self.places)
                    else:
                        self.reannounce_lap()
                    return True
                elif key == key_placesto:
                    self.fill_places_to_selected()
                    return True
                elif key == key_appendplace:
                    self.append_selected_place()
                    return True
        return False

    def append_selected_place(self):
        sel = self.view.get_selection()
        sv = sel.get_selected()
        if sv is not None:
            i = sv[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            _log.debug('Confirmed next place: %s [%s]', selbib, selpath)
            nplaces = []
            # remove selected rider from places
            for placegroup in self.places.split():
                gv = placegroup.split('-')
                try:
                    gv.remove(selbib)
                except Exception:
                    pass
                if gv:
                    nplaces.append('-'.join(gv))
            # append selected rider to places and recalc
            nplaces.append(selbib)
            self.places = ' '.join(nplaces)
            self.recalculate()
            # advance selection
            j = self.riders.iter_next(i)
            if j is not None:
                lr = Gtk.TreeModelRow(self.riders, j)
                if lr[COL_PLACE] or lr[COL_RFTIME] is not None or lr[
                        COL_MBUNCH] is not None:
                    self.view.set_cursor(lr.path, None, False)

    def fill_places_to_selected(self):
        """Update places to match ordering up to selected rider."""
        if '-' in self.places:
            _log.warning('Dead heat in result, places not updated')
            return False
        sel = self.view.get_selection()
        sv = sel.get_selected()
        if sv is not None:
            # fill places and recalculate
            i = sv[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            _log.info('Confirm places to: %s [%s]', selbib, selpath)
            oplaces = self.places.split()
            nplaces = []
            atbreak = False
            for r in self.riders:
                rbib = r[COL_BIB]
                if not atbreak:
                    # re-order places up to selection
                    if rbib in oplaces:
                        oplaces.remove(rbib)
                    if r[COL_INRACE]:
                        nplaces.append(rbib)
                else:
                    # retain ordering after selection, removing dnf
                    if rbib in oplaces and not r[COL_INRACE]:
                        oplaces.remove(rbib)
                if rbib == selbib:  # break after to get sel rider
                    atbreak = True
            nplaces.extend(oplaces)
            self.places = ' '.join(nplaces)
            self.recalculate()
            # advance selection if next rider is finished
            j = self.riders.iter_next(i)
            if j is not None:
                lr = Gtk.TreeModelRow(self.riders, j)
                if lr[COL_PLACE] or lr[COL_RFTIME] is not None or lr[
                        COL_MBUNCH] is not None:
                    self.view.set_cursor(lr.path, None, False)

    def clear_places_from_selection(self):
        """Clear all places from riders following the current selection."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            _log.info('Clear places from: %s [%s]', selbib, selpath)
            nplaces = []
            found = False
            for placegroup in self.places.split():
                newgroup = []
                for r in placegroup.split('-'):
                    if r == selbib:
                        found = True
                        break
                    newgroup.append(r)
                if newgroup:
                    nplaces.append('-'.join(newgroup))
                if found:
                    break
            self.places = ' '.join(nplaces)
            self.recalculate()

    def clear_place(self, bib):
        nplaces = []
        for placegroup in self.places.split():
            gv = placegroup.split('-')
            try:
                gv.remove(bib)
            except Exception:
                pass
            if gv:
                nplaces.append('-'.join(gv))
        self.places = ' '.join(nplaces)

    def clear_selected_place(self):
        """Remove the selected rider from places."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            selpath = self.riders.get_path(i)
            _log.info('Clear rider from places: %s [%s]', selbib, selpath)
            self.clear_place(selbib)
            self.recalculate()

    def dnfriders(self, biblist='', code='dnf'):
        """Remove each rider from the event with supplied code."""
        recalc = False
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                if code != 'wd':
                    r[COL_INRACE] = False
                    r[COL_CBUNCH] = None
                    r[COL_MBUNCH] = None
                r[COL_COMMENT] = code
                recalc = True
                _log.info('Rider %s did not finish with code: %s', bib, code)
            else:
                _log.warning('Unregistered rider %s unchanged', bib)
        if recalc:
            self.recalculate()
        return False

    def rms_context_lap_leader_cb(self, menuitem, data=None):
        """Set event level lap times based on selected rider"""
        if self.start is not None:
            if self.timerstat != 'finished':
                sel = self.view.get_selection().get_selected()
                bib = None
                if sel is not None:
                    i = sel[1]
                    r = Gtk.TreeModelRow(self.riders, i)
                    if len(r[COL_RFSEEN]) > 0:
                        self.newlapleader(r)
                    else:
                        _log.info('Lap leader ignored: No passings')
                else:
                    _log.info('Lap leader ignored: Empty selection')
            else:
                _log.info('Lap leader ignored: Event finsihed')
        else:
            _log.info('Lap leader ignored: Event not started')

    def newlapleader(self, lr):
        """Update event lap times"""
        lapcount = len(lr[COL_RFSEEN])
        lapstart = None
        lapfin = None
        if lapcount > 0:
            lapfin = lr[COL_RFSEEN][-1]
        if lapcount > 1:
            lapstart = lr[COL_RFSEEN][-2]
        self.lapstart = lapstart
        self.lapfin = lapfin
        self.curlap = lapcount
        self.onlap = lapcount + 1
        self.lapentry.set_text(str(self.curlap))
        self.finish = None
        _log.info('Updated lap leader from rider %s', lr[COL_BIB])

    def manpassing(self, biblist=''):
        """Register a manual passing, preserving order."""
        for bib in biblist.split():
            rno, rser = strops.bibstr2bibser(bib)
            if not rser:  # allow series manual override
                rser = self.series
            bibstr = strops.bibser2bibstr(rno, rser)
            t = tod.now()
            t.chan = 'MAN'
            t.refid = 'riderno:' + bibstr
            self.meet._timercb(t)
            _log.debug('Manual passing: %r', bibstr)

    def updateteam(self, team=None):
        """Handle a change in teams data"""
        pass

    def ridercb(self, rider):
        """Handle a change in the rider model"""
        if rider is not None:
            if rider[1] == self.series:
                bib = rider[0]
                lr = self.getrider(bib)
                if lr is not None:
                    r = self.meet.rdb[rider]
                    self.updaterider(lr, r)
                    _log.debug('Updated single rider %s', rider)
                else:
                    _log.debug('Ignored update on non-starter %s', rider)
                # a change in team may alter team data mapping
                self.updateteam(rider)
            elif rider[1] == 'cat':
                # if cat is a result category in this event
                if self.ridercat(rider[0]):
                    self.load_cat_data()
            elif rider[1] == 'team':
                # team changes may require recalc
                self.updateteam(rider)
            else:
                _log.debug('Ignore out of series rider %s', rider)
        else:
            _log.debug('Update all cats')
            self.load_cat_data()
            _log.debug('Update all riders')
            count = 0
            for lr in self.riders:
                bib = lr[COL_BIB]
                r = self.meet.rdb.get_rider(bib, self.series)
                if r is not None:
                    self.updaterider(lr, r)
                    count += 1
                else:
                    _log.debug('Ignored rider not in riderdb %s', bib)
            _log.debug('Updated %d riders', count)
            _log.debug('Update teams')
            self.updateteam()

    def retriders(self, biblist=''):
        """Return all listed riders to the event."""
        recalc = False
        for bib in biblist.split():
            r = self.getrider(bib)
            if r is not None:
                r[COL_INRACE] = True
                r[COL_COMMENT] = ''
                r[COL_LAPS] = len(r[COL_RFSEEN])
                recalc = True
                _log.info('Rider %s returned to event', bib)
            else:
                _log.warning('Unregistered rider %s unchanged', bib)
        if recalc:
            self.recalculate()
        return False

    def shutdown(self, win=None, msg='Race Sutdown'):
        """Close event."""
        _log.debug('Event shutdown: %r', msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def starttrig(self, e):
        """Process a start trigger signal."""
        # Note: in rms all triggers other than C1 from alttimer
        #       are assumed to be for the start
        if self.timerstat == 'armstart':
            _log.info('Start trigger: %s@%s/%s', e.chan, e.rawtime(), e.source)
            self.set_start(e)
            self.resetcatonlaps()
            if self.etype in ('criterium', 'circuit', 'cross'):
                GLib.idle_add(self.armlap)
        else:
            _log.info('Trigger: %s@%s/%s', e.chan, e.rawtime(), e.source)
        return False

    def set_lap_finish(self, e):
        """Write lap time into model and emit changed state."""
        self.laptimes.append(e)
        self.lapfin = e
        if self.totlaps is not None:
            if self.onlap == self.totlaps:
                self.onlap = None
            else:
                self.onlap += 1
        else:
            self.onlap += 1
        self.reannounce_times()

    def alttimertrig(self, e):
        """Record timer message from alttimer."""
        # note: these impulses are sourced from alttimer device and keyboard
        #       transponder triggers are collected separately in timertrig()
        _log.debug('Alt timer: %s@%s/%s', e.chan, e.rawtime(), e.source)
        channo = strops.chan2id(e.chan)
        if channo == 1:
            _log.info('Trigger: %s@%s/%s', e.chan, e.rawtime(), e.source)
            # if finish armed, treat as bunch time
            if self.timerstat == 'armfinish':
                if self.altfinish is not None:
                    dt = e - self.altfinish
                    _log.info('New bunch time: +%s', dt.rawtime(0))
                else:
                    _log.debug('Recording first bunch finish: %s', e.rawtime())
                    self.altfinish = e
        else:
            # send through to catch-all trigger handler
            self.starttrig(e)

    def catstarted(self, cat):
        """Return true if category is considered started."""
        ret = False
        if self.start is not None:
            stof = tod.ZERO
            if cat in self.catstarts and self.catstarts[cat] is not None:
                stof = self.catstarts[cat]
            st = self.start + stof
            nt = tod.now()
            if st < nt:
                if cat:
                    _log.debug('Cat %s has started', cat)
                ret = True
            else:
                if cat:
                    _log.debug('Cat %s has not yet started: %s < %s', cat,
                               nt.rawtime(1), st.rawtime(1))
        return ret

    def announcecatlap(self, acat=None):
        """Emit a category lap scoreboard message."""
        for cat in self.cats:
            if cat == acat or (acat is None and cat):
                if cat in self.catonlap:
                    count = self.catonlap[cat]
                    onlap = count
                    if self.catstarted(cat):
                        onlap += 1
                    target = self.totlaps
                    togo = None
                    if cat in self.catlaps and self.catlaps[cat] is not None:
                        target = self.catlaps[cat]
                    if target is not None and count is not None and count < target:
                        prompt = cat.upper()
                        dbr = self.meet.rdb.get_rider(cat, 'cat')
                        if dbr is not None:
                            prompt = dbr['lap prefix']
                        self.meet.cmd_announce(
                            'catlap', '\x1f'.join([
                                cat, prompt,
                                str(onlap),
                                str(target),
                                str(target - onlap)
                            ]))
                        if cat:
                            _log.warning('Cat %s %r: %d/%d, %d to go', cat,
                                         prompt, onlap, target, target - onlap)
                    else:
                        _log.debug('No data for Cat %s laps', cat)

    def timertrig(self, e):
        """Process transponder passing event."""

        # all impulses from transponder timer are considered start triggers
        if e.refid in ('', '255'):
            return self.starttrig(e)

        # fetch rider data from riderdb using refid lookup
        r = self.meet.getrefid(e.refid)
        if r is None:
            _log.info('Unknown rider: %s:%s@%s/%s', e.refid, e.chan,
                      e.rawtime(2), e.source)
            return False

        bib = r['no']
        ser = r['series']
        if ser != self.series:
            _log.info('Non-series rider: %s.%s', bib, ser)
            return False

        # if there's a channel id filter set, discard unknown channel
        if self.passingsource is not None:
            chan = strops.chan2id(e.chan)
            if chan >= 0 and chan != self.passingsource:
                _log.info('Invalid channel passing: %s:%s@%s/%s', bib, e.chan,
                          e.rawtime(2), e.source)
                return False

        # check for a spare bike in riderdb cat, before clubmode additions
        spcat = r.primary_cat()
        if self.allowspares and spcat == 'SPARE' and self.timerstat in (
                'running', 'armfinish'):
            _log.warning('Adding spare bike: %s', bib)
            self.addrider(bib)

        # fetch event info for rider
        lr = self.getrider(bib)
        if lr is None:
            if self.clubmode and self.timerstat in ('armstart', 'running',
                                                    'armfinish'):
                ri = self.addrider(bib)
                lr = Gtk.TreeModelRow(self.riders, ri)
                _log.info('Added new starter: %s:%s@%s/%s', bib, e.chan,
                          e.rawtime(2), e.source)
            else:
                _log.info('Non-starter: %s:%s@%s/%s', bib, e.chan,
                          e.rawtime(2), e.source)
                return False

        # log passing of rider before further processing
        if not lr[COL_INRACE]:
            _log.warning('Withdrawn rider: %s:%s@%s/%s', bib, e.chan,
                         e.rawtime(2), e.source)
            # but continue as if still in event
        else:
            _log.info('Saw: %s:%s@%s/%s', bib, e.chan, e.rawtime(2), e.source)

        # check run state
        if self.timerstat in ('idle', 'armstart', 'finished'):
            return False

        # fetch primary category IN event
        rcat = self.ridercat(r.primary_cat())

        # check for start and minimum passing time
        st = tod.ZERO
        catstart = tod.ZERO
        if lr[COL_STOFT] is not None:
            # start offset in riders model overrides cat start
            catstart = lr[COL_STOFT]
        elif rcat in self.catstarts and self.catstarts[rcat] is not None:
            catstart = self.catstarts[rcat]
        if self.start is not None:
            st = self.start + catstart + self.minlap
        # ignore all passings from before first allowed time
        if e <= st:
            _log.info('Ignored early passing: %s:%s@%s/%s < %s', bib, e.chan,
                      e.rawtime(2), e.source, st.rawtime(2))
            return False

        # check this passing against previous passing records
        lastchk = None
        ipos = bisect.bisect_right(lr[COL_RFSEEN], e)
        if ipos == 0:  # first in-race passing, accept
            pass
        else:  # always one to the 'left' of e
            # check previous passing for min lap time
            lastseen = lr[COL_RFSEEN][ipos - 1]
            nthresh = lastseen + self.minlap
            if e <= nthresh:
                _log.info('Ignored short lap: %s:%s@%s/%s < %s', bib, e.chan,
                          e.rawtime(2), e.source, nthresh.rawtime(2))
                return False
            # check the following passing if it exists
            if len(lr[COL_RFSEEN]) > ipos:
                npass = lr[COL_RFSEEN][ipos]
                delta = npass - e
                if delta <= self.minlap:
                    _log.info('Spurious passing: %s:%s@%s/%s < %s', bib,
                              e.chan, e.rawtime(2), e.source, npass.rawtime(2))
                    return False

        # insert this passing in order
        lr[COL_RFSEEN].insert(ipos, e)

        # update event model if rider still in race
        if lr[COL_RFTIME] is None:
            return self.riderlap(bib, lr, rcat, e)
        else:
            _log.info('Ignored finished rider: %s:%s@%s/%s', bib, e.chan,
                      e.rawtime(2), e.source)

        return False

    def eventlap(self, bib, lr, rcat, e):
        """Update event lap counts based on rider passing"""
        onlap = False
        if self.lapfin is None:
            # lap finish armed, first rider with laps == curlap
            # will be considered the leader, otherwise they are dropped
            # NOTE: this overrides lap time guards
            if lr[COL_LAPS] == self.curlap:
                self.set_lap_finish(e)
                self.meet.cmd_announce('redraw', 'timer')
                onlap = True
        else:
            curlapstart = self.lapfin
            if e < curlapstart:
                # passing is for a previous event lap
                onlap = False
                _log.info('Passing on previous lap: %s:%s@%s/%s < %s',
                          bib, e.chan, e.rawtime(2), e.source,
                          curlapstart.rawtime(2))
            else:
                if lr[COL_LAPS] == self.curlap:
                    onlap = True
                elif lr[COL_LAPS] < self.curlap:
                    if self.etype == 'criterium':
                        # push them on to the current lap
                        lr[COL_LAPS] = self.curlap
                        onlap = True
                    else:
                        # rider is not on current event lap
                        pass
                else:
                    if e < curlapstart + self.minlap:
                        # passing cannot be for a new lap yet
                        if self.etype == 'criterium':
                            # push them back to the current lap
                            lr[COL_LAPS] = self.curlap
                            onlap = True
                        else:
                            if self.curlap > 0:
                                _log.warning(
                                    'Rider/event lap mismatch %s: %d != %d',
                                    bib, lr[COL_LAPS], self.curlap)
                    else:
                        # otherwise this is the lap leader
                        self.armlap()
                        self.set_lap_finish(e)
                        self.meet.cmd_announce('redraw', 'timer')
                        onlap = True
        return onlap

    def riderlap(self, bib, lr, rcat, e):
        """Process an accepted rider lap passing"""
        # check if lap mode is target-based
        lapfinish = False
        doarm = False
        targetlap = None
        if self.autofinish:
            # category laps override event laps
            if rcat in self.catlaps and self.catlaps[rcat]:
                targetlap = self.catlaps[rcat]
            else:
                targetlap = self.totlaps
            if targetlap and lr[COL_LAPS] >= targetlap - 1:
                lapfinish = True  # arm just this rider
                if self.etype == 'cross':
                    doarm = True

        # for cross events when targets apply, armfinish is set automatically
        if doarm and lapfinish and self.timerstat != 'armfinish':
            self.armfinish()

        # finishing rider path
        if self.timerstat == 'armfinish' or lapfinish:
            if lr[COL_COMMENT] != 'wd':
                if lr[COL_PLACE] == '':
                    lr[COL_RFTIME] = e
                    self._dorecalc = True
                else:
                    _log.error('Placed rider seen at finish: %s:%s@%s/%s', bib,
                               e.chan, e.rawtime(2), e.source)
                if lr[COL_INRACE]:
                    lr[COL_LAPS] += 1
                    if rcat in self.catonlap and lr[COL_LAPS] > self.catonlap[
                            rcat]:
                        self.catonlap[rcat] = lr[COL_LAPS]
                        self.announcecatlap(rcat)
                    self.announce_rider('', bib, lr[COL_NAMESTR], lr[COL_CAT],
                                        e, lr[COL_LAPS])
            self.eventlap(bib, lr, rcat, e)
        # end finishing rider path

        # lapping rider path
        elif self.timerstat == 'running':
            self._dorecalc = True
            if lr[COL_INRACE] and (lr[COL_PLACE] or lr[COL_CBUNCH] is None):
                # rider in the event, not yet finished: increment own lap count
                lr[COL_LAPS] += 1
                onlap = False

                # category and target lap counting
                if self.autofinish:
                    catlap = 0
                    if rcat in self.catonlap:
                        catlap = self.catonlap[rcat]
                    else:
                        self.catonlap[rcat] = 0  # init

                    if lr[COL_LAPS] > catlap:
                        self.catonlap[rcat] = lr[COL_LAPS]
                        self.announcecatlap(rcat)
                    else:
                        if lr[COL_LAPS] >= catlap:
                            # rider is on the current event lap
                            onlap = True

                self.eventlap(bib, lr, rcat, e)
        else:
            _log.debug('Ignored rider lap for timerstat=%s', self.timerstat)

        # announce all rider passings
        self.announce_rider('', bib, lr[COL_NAMESTR], lr[COL_CAT], e,
                            lr[COL_LAPS])
        return False

    def announce_rider(self, place, bib, namestr, cat, rftime, lap=None):
        """Log a rider in the lap and emit to announce."""
        if bib not in self.scratch_map:
            self.scratch_map[bib] = rftime
            self.scratch_ord.append(bib)
        if self.live_announce:
            if lap is not None:
                lap = str(lap)
            else:
                lap = ''
            GLib.idle_add(self.meet.rider_announce,
                          [place, bib, namestr, cat,
                           rftime.rawtime(), lap])

    def lapentry_activate_cb(self, entry, data=None):
        """Transfer lap entry string into model if possible."""
        reqlap = entry.get_text()
        newlap = None
        try:
            newlap = int(reqlap)
        except Exception:
            _log.debug('Invalid lap count %r', reqlap)

        if newlap is not None and newlap > 0:
            if self.etype == 'criterium':
                # force all in riders onto the desired lap
                for r in self.riders:
                    if r[COL_INRACE]:
                        r[COL_LAPS] = newlap - 1
            else:
                # correct all rider lap counts, saturated at desired lap
                for r in self.riders:
                    olap = len(r[COL_RFSEEN])
                    if r[COL_INRACE]:
                        if olap > newlap - 1:
                            olap = newlap - 1
                    r[COL_LAPS] = olap
            if self.lapfin is not None:
                self.curlap = newlap - 1
            else:
                self.curlap = newlap
            self.armlap()
        else:
            self.curlap = -1
            self.lapstart = None
            self.lapfin = None
            maxlap = 1
            entry.set_text('')
            if self.timerstat not in ('idle', 'armstart', 'finished'):
                maxlap = 0
                for r in self.riders:
                    r[COL_LAPS] = len(r[COL_RFSEEN])
                    maxlap = max(r[COL_LAPS] + 1, maxlap)
            self.onlap = maxlap
            if self.etype in ('criterium', 'circuit', 'cross'):
                self.armlap()

    def totlapentry_activate_cb(self, entry, data=None):
        """Transfer total lap entry string into model if possible."""
        try:
            nt = entry.get_text()
            if nt:  # not empty
                self.totlaps = int(nt)
            else:
                self.totlaps = None
        except Exception:
            _log.warning('Ignored invalid total lap count')
        if self.totlaps is not None:
            self.totlapentry.set_text(str(self.totlaps))
        else:
            self.totlapentry.set_text('')

    def finsprint(self, places):
        """Display a final sprint 'provisional' result."""
        self.live_announce = False
        self.meet.cmd_announce('clear', 'all')
        scrollmsg = 'Provisional - '
        titlestr = self.title_namestr.get_text()
        if self.racestat == 'final':
            scrollmsg = 'Result - '
            self.meet.cmd_announce('title', titlestr + ': Final Result')
        else:
            self.meet.cmd_announce('title', titlestr + ': Provisional')
        self.meet.cmd_announce('bunches', 'final')
        placeset = set()
        idx = 0
        st = tod.ZERO
        if self.start is not None:
            st = self.start
        # result elapsed time is sent in hybrid units
        wt = None
        lb = None
        scrollcnt = 0
        for placegroup in places.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is not None:
                        ft = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                        fs = ''
                        if ft is not None:
                            # temp -> just use the no-blob style to correct
                            fs = ft.rawtime()
                            if wt is None:
                                wt = ft
                            lb = ft
                        if scrollcnt < 5:
                            scrollmsg += (' ' + r[COL_PLACE] + '. ' +
                                          r[COL_SHORTNAME] + ' ')
                            scrollcnt += 1
                        GLib.idle_add(self.meet.rider_announce, [
                            r[COL_PLACE] + '.', bib, r[COL_NAMESTR],
                            r[COL_CAT], fs
                        ])
                    idx += 1
        self.meet.cmd_announce('scrollmsg', scrollmsg)
        if wt is not None:
            if self.start:
                self.meet.cmd_announce('start', self.start.rawtime())
            if self.finish:
                self.meet.cmd_announce('finish', self.finish.rawtime())
            else:
                _log.info('No valid times available')

    def int_report(self, acode, catcache):
        """Return report sections for the named intermed."""
        ret = []
        if acode not in self.intermeds:
            _log.debug('Attempt to read non-existent inter: %r', acode)
            return ret
        descr = acode
        if self.intermap[acode]['descr']:
            descr = self.intermap[acode]['descr']
        places = self.intermap[acode]['places']
        points = None
        # for 1-1 intermed/contest entries, copy points to inter report
        if acode in self.contestmap:
            if len(self.contestmap[acode]['points']) > 1:
                points = self.contestmap[acode]['points']
        lines = []
        placeset = set()
        idx = 0
        dotime = False
        if 'time' in self.intermap[acode]['descr'].lower():
            dotime = True
        for placegroup in places.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is not None:
                        cs = r[COL_CAT]
                        rcat = self.ridercat(riderdb.primary_cat(cs))
                        cstr = rcat
                        # in handicap result, cat overrides UCIID
                        if cstr.upper() in catcache:
                            cstr = catcache[cstr.upper()]
                        xtra = None
                        if dotime:
                            bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                            if bt is not None:
                                st = self.getstart(r)
                                if st is not None:
                                    bt = bt - st
                                xtra = bt.rawtime(0)
                        elif points is not None:
                            if curplace <= len(points):
                                xtra = str(points[curplace - 1])
                        lines.append([
                            str(curplace) + '.', bib, r[COL_NAMESTR], cstr,
                            None, xtra
                        ])
                    idx += 1
                else:
                    _log.warning('Duplicate in places: %s', bib)
        if len(lines) > 0:
            sec = report.section('inter' + acode)
            sec.heading = descr
            sec.lines = lines
            if points is not None:
                sec.units = 'pt'
            ret.append(sec)
        return ret

    def intsprint(self, acode='', places=''):
        """Display an intermediate sprint 'provisional' result."""

        if acode not in self.intermeds:
            _log.debug('Attempt to display non-existent inter: %r', acode)
            return
        descr = acode
        if self.intermap[acode]['descr']:
            descr = self.intermap[acode]['descr']
        self.live_announce = False
        self.meet.cmd_announce('clear', 'all')
        self.reannounce_times()
        self.meet.cmd_announce('title', descr)
        scrollmsg = descr + ' - '
        placeset = set()
        idx = 0
        for placegroup in places.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is not None:
                        scrollmsg += (' ' + str(curplace) + '. ' +
                                      r[COL_SHORTNAME] + ' ')
                        rank = ''
                        if acode != 'brk':
                            rank = str(curplace) + '.'
                        GLib.idle_add(
                            self.meet.rider_announce,
                            [rank, bib, r[COL_NAMESTR], r[COL_CAT], ''])
                    idx += 1
                else:
                    _log.warning('Duplicate in places: %s', bib)
        GLib.timeout_add_seconds(25, self.reannounce_lap)

    def todempty(self, val):
        if val is not None:
            return val.rawtime()
        else:
            return ''

    def reannounce_times(self):
        """Re-send the current timing values."""
        self.meet.cmd_announce('gapthresh', self.gapthresh.rawtime(2))
        self.meet.cmd_announce('timerstat', self.timerstat)
        self.meet.cmd_announce('start', self.todempty(self.start))
        self.meet.cmd_announce('finish', self.todempty(self.finish))
        self.meet.cmd_announce('lapstart', self.todempty(self.lapstart))
        self.meet.cmd_announce('lapfin', self.todempty(self.lapfin))
        totlaps = None
        if self.totlaps:  #may be zero, but don't show that case
            totlaps = str(self.totlaps)
        curlap = None
        if self.curlap is not None:
            curlap = str(self.curlap)
        onlap = None
        if self.onlap is not None:
            onlap = str(self.onlap)
        self.meet.cmd_announce('onlap', onlap)
        self.meet.cmd_announce('curlap', curlap)
        self.meet.cmd_announce('totlaps', totlaps)
        # Write lap time fields
        lapstr = None
        if self.timerstat not in ('armfinish', 'finished'):
            self.meet.cmd_announce('finstr', None)
            if self.onlap is not None:
                if self.totlaps is not None and self.totlaps > 0:
                    lapstr = ('Lap ' + str(self.onlap) + '/' +
                              str(self.totlaps))
                    self.totlapentry.set_text(str(self.totlaps))
                else:  # 0 flags unknown total
                    lapstr = ''
                    passkey = str(self.curlap)
                    if passkey in self.passlabels:
                        lapstr = 'At ' + self.passlabels[passkey]
                    else:
                        if self.etype in ('circuit', 'criterium', 'cross'):
                            lapstr = ('Lap ' + str(self.onlap))
                self.meet.cmd_announce('laplbl', lapstr)
            else:
                # make sure something is displayed in this path
                self.meet.cmd_announce('laplbl', None)
                self.meet.cmd_announce('finstr', self.meet.get_short_name())

        if self.timerstat == 'idle':
            self.meet.cmd_announce('finstr', self.meet.get_short_name())
        return False

    def reannounce_lap(self):
        """Re-send current lap passing data."""
        self.meet.cmd_announce('clear', 'all')
        self.meet.cmd_announce('scrollmsg', None)
        self.meet.cmd_announce('bunches', 'laps')
        self.reannounce_times()
        self.live_announce = False
        if self.timerstat == 'armfinish':
            self.meet.cmd_announce('title', 'Finish')
        else:
            self.meet.cmd_announce('title', self.title_namestr.get_text())
        for bib in self.scratch_ord:
            r = self.getrider(bib)
            if r is not None:
                GLib.idle_add(self.meet.rider_announce, [
                    '', bib, r[COL_NAMESTR], r[COL_CAT],
                    self.scratch_map[bib].rawtime()
                ])
        self.live_announce = True
        return False

    def timeout(self):
        """Update elapsed time and recalculate if required."""
        if not self.winopen:
            return False
        if self._dorecalc:
            self.recalculate()
            if self.autoexport:
                GLib.idle_add(self.meet.menu_data_results_cb, None)
        et = None
        nt = None
        if self.start is not None and self.timerstat != 'finished':
            nt = tod.now()
            et = nt - self.start
        if et is not None:
            evec = []
            if self.finish is not None:
                # event down time is on first finisher
                ft = (self.finish - self.start).truncate(0)
                evec.append(ft.rawtime(0))
                rt = et - ft
                if rt < MAXELAP:
                    evec.append('+' + rt.rawtime(0))

                # time limit is applied to total event time/first finisher
                limit = self.decode_limit(self.timelimit, ft)
                if limit is not None:
                    evec.append('Limit: +' + (limit - ft).rawtime(0))
            else:
                evec.append(et.rawtime(0))
                if self.lapfin is not None:
                    # prev lap time
                    if self.lapstart is not None:
                        evec.append('Lap: ' +
                                    (self.lapfin - self.lapstart).rawtime(0))
                    # lap down time
                    dt = nt - self.lapfin
                    if dt < MAXELAP:
                        evec.append('+' + dt.rawtime(0))
            elapmsg = '\u2003'.join(evec)
            self.elaplbl.set_text(elapmsg)
            self.meet.cmd_announce('elapmsg', elapmsg)
        else:
            self.elaplbl.set_text('')
            self.meet.cmd_announce('elapmsg', '')
        return True

    def set_start(self, start=None):
        """Set the start time."""
        wasidle = self.start is None
        if start is not None:
            if type(start) is not tod.tod:
                _log.warning('Ignored invalid start time %r', start)
                start = None
        self.start = start
        if self.start is not None:
            if wasidle:
                self.lapstart = None
                self.lapfin = None
                self.curlap = -1  # reset lap count at start
                self.onlap = 1  # leaders are 'onlap'
                self.meet.cmd_announce('onlap', str(self.onlap))
            if self.finish is None:
                self.set_running()
            self.meet.cmd_announce('start', self.start.rawtime())
        else:
            self.meet.cmd_announce('start', None)

    def set_finish(self, finish=None):
        """Set the finish time."""
        if self.etype != 'handicap' and len(self.cats) > 1:
            # Don't set event finish for multi-cat event
            _log.debug('Set finish skipped for multi-cat event')
            return

        if finish is not None:
            if type(finish) is not tod.tod:
                _log.warning('Ignored invalid finish time %r', finish)
                finish = None
        self.finish = finish
        if self.finish is None:
            if self.start is not None:
                self.set_running()
        else:
            self.meet.cmd_announce('finish', self.finish.rawtime())
            if self.start is None:
                self.set_start(tod.ZERO)
            else:
                elap = self.finish - self.start
                dval = self.meet.get_distance()
                if dval is not None:
                    self.meet.cmd_announce('average',
                                           elap.rawspeed(1000.0 * dval))

    def get_elapsed(self):
        """Return time from start."""
        ret = None
        if self.start is not None and self.timerstat != 'finished':
            ret = (tod.now() - self.start).truncate(0)
        return ret

    def set_running(self):
        """Update event status to running."""
        self.timerstat = 'running'
        self.meet.cmd_announce('timerstat', 'running')
        self.meet.stat_but.update('ok', 'Running')

    def set_finished(self):
        """Update event status to finished."""
        self.timerstat = 'finished'
        self.meet.cmd_announce('timerstat', 'finished')
        self.meet.cmd_announce('laplbl', None)
        self.meet.stat_but.update('idle', 'Finished')
        self.meet.stat_but.set_sensitive(False)

    def info_time_edit_clicked_cb(self, button, data=None):
        """Run an edit times dialog to update event time."""
        sections = {
            'times': {
                'object': None,
                'title': 'times',
                'schema': {
                    'title': {
                        'prompt': 'Manually adjust event time',
                        'control': 'section',
                    },
                    'start': {
                        'prompt': 'Start:',
                        'hint': 'Event start time',
                        'type': 'tod',
                        'places': 4,
                        'control': 'short',
                        'nowbut': True,
                        'value': self.start,
                    },
                    'finish': {
                        'prompt': 'Finish:',
                        'hint': 'Event finish time',
                        'type': 'tod',
                        'places': 4,
                        'control': 'short',
                        'nowbut': True,
                        'value': self.finish,
                    },
                },
            },
        }
        res = uiutil.options_dlg(window=self.meet.window,
                                 title='Edit times',
                                 sections=sections)
        if res['times']['start'][0] or res['times']['finish'][0]:
            wasrunning = self.timerstat in ('running', 'armfinish')
            self.set_finish(res['times']['finish'][2])
            self.set_start(res['times']['start'][2])
            if wasrunning:
                # flag a recalculate
                self._dorecalc = True
            else:
                self.resetcatonlaps()
                if self.etype in ('criterium', 'circuit', 'cross'):
                    GLib.idle_add(self.armlap)
            _log.info('Manually adjusted event times')
        else:
            _log.debug('Edit times: No change')

    def editcol_cb(self, cell, path, new_text, col):
        """Edit column callback."""
        new_text = new_text.strip()
        self.riders[path][col] = new_text

    def editlap_cb(self, cell, path, new_text, col):
        """Edit the lap field if valid."""
        new_text = new_text.strip()
        if new_text == '?':
            self.riders[path][col] = len(self.riders[path][COL_RFSEEN])
        elif new_text.isdigit():
            self.riders[path][col] = int(new_text)
        else:
            _log.error('Invalid lap count')

    def editname_cb(self, cell, path, new_text, col):
        """Edit the rider name if possible."""
        old_text = self.riders[path][col].lower()
        if new_text and old_text != new_text:
            first = []
            last = []
            org = []
            inorg = False
            for namebit in new_text.split():
                if namebit.startswith('('):
                    inorg = True
                if inorg:
                    org.append(namebit.replace('(', '').replace(')', ''))
                    if namebit.endswith(')'):
                        inorg = False
                elif first:
                    last.append(namebit)
                else:
                    first.append(namebit)
            r = self.meet.rdb.get_rider(self.riders[path][COL_BIB],
                                        self.series)
            if r is not None:
                notify = False
                if first:
                    r.set_value('first', ' '.join(first))
                    notify = True
                if last:
                    r.set_value('last', ' '.join(last))
                    notify = True
                if org:
                    r.set_value('org', ' '.join(org))
                    notify = True
                if notify:
                    r.notify()
            else:
                _log.debug('Rider %s not found in riders list',
                           self.riders[path][COL_BIB])
                self.riders[path][col] = new_text

    def editcat_cb(self, cell, path, new_text, col):
        """Edit the cat field if valid."""
        new_text = ' '.join(new_text.strip().upper().split())
        r = self.meet.rdb.get_rider(self.riders[path][COL_BIB], self.series)
        if r is not None:
            # note: this will generate a rider change callback
            r['cat'] = new_text
        self.riders[path][col] = new_text

    def editseed_cb(self, cell, path, new_text, col):
        """Edit the lap field if valid."""
        new_text = new_text.strip()
        if new_text.isdigit():
            self.riders[path][col] = int(new_text)
        else:
            _log.error('Invalid lap count')

    def resetplaces(self):
        """Clear places off all riders."""
        for r in self.riders:
            r[COL_PLACE] = ''
        self.bonuses = {}  # bonuses are global to stage
        for c in self.tallys:  # points are grouped by tally
            self.points[c] = {}
            self.pointscb[c] = {}

    def vbunch(self, cbunch=None, mbunch=None):
        """Switch to return best choice bunch time."""
        ret = None
        if mbunch is not None:
            ret = mbunch
        elif cbunch is not None:
            ret = cbunch
        return ret

    def getstart(self, r):
        """Return a start offset"""
        ret = tod.ZERO
        if r[COL_STOFT] is not None:
            ret = r[COL_STOFT]
        else:
            # Check primary category for start time
            cs = r[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if rcat in self.catstarts and self.catstarts[rcat] is not None:
                ret = self.catstarts[rcat]
        return ret

    def showstart_cb(self, col, cr, model, iter, data=None):
        """Draw start time offset in rider view."""
        st = model.get_value(iter, COL_STOFT)
        otxt = ''
        if st is not None:
            otxt = st.rawtime(0)
        else:
            # cat start comes from first category only
            cs = model.get_value(iter, COL_CAT)
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if rcat in self.catstarts and self.catstarts[rcat] is not None:
                otxt = self.catstarts[rcat].rawtime(0)
        cr.set_property('text', otxt)

    def edit_event_properties(self, window, data=None):
        """Edit event specifics."""
        # set current event type label
        _CONFIG_SCHEMA['etype']['prompt'] = ROADRACE_TYPES[self.etype]

        # flatten current cat list
        _CONFIG_SCHEMA['categories']['value'] = ' '.join(
            self.get_catlist()).strip()
        res = uiutil.options_dlg(window=self.meet.window,
                                 title='Event Properties',
                                 sections={
                                     'event': {
                                         'title': 'Event',
                                         'schema': _CONFIG_SCHEMA,
                                         'object': self,
                                     },
                                 })
        # handle a change in result categories
        if res['event']['categories'][0]:
            self.loadcats(res['event']['categories'][2].upper().split())
            self.load_cat_data()
            if len(self.cats) > 1:
                _log.info('Loaded result categories: %s',
                          ', '.join(self.cats[0:-1]))
            else:
                _log.info('Result categories cleared')
        return False

    def getbunch_iter(self, iter):
        """Return a 'bunch' string for the rider."""
        cmt = self.riders.get_value(iter, COL_COMMENT)
        place = self.riders.get_value(iter, COL_PLACE)
        lap = self.riders.get_value(iter, COL_LAPS)
        cb = self.riders.get_value(iter, COL_CBUNCH)
        mb = self.riders.get_value(iter, COL_MBUNCH)
        tv = ''
        if mb is not None:
            tv = mb.rawtime(0)
        else:
            if cb is not None:
                tv = cb.rawtime(0)
            else:
                # just show event elapsed in this path
                seen = self.riders.get_value(iter, COL_RFSEEN)
                if len(seen) > 0:
                    et = seen[-1]
                    if self.start:
                        et -= self.start
                    tv = '[' + et.rawtime(1) + ']'
        rv = []
        if place:
            rv.append('{}.'.format(place))
        elif cmt:
            rv.append(cmt)
        if lap > 0:
            rv.append('Lap:{}'.format(lap))
        if tv:
            rv.append(tv)
        return ' '.join(rv)

    def showbunch_cb(self, col, cr, model, iter, data=None):
        """Update bunch time on rider view."""
        cb = model.get_value(iter, COL_CBUNCH)
        mb = model.get_value(iter, COL_MBUNCH)
        if mb is not None:
            cr.set_property('text', mb.rawtime(0))
            cr.set_property('style', uiutil.STYLE_ITALIC)
        else:
            cr.set_property('style', uiutil.STYLE_NORMAL)
            if cb is not None:
                cr.set_property('text', cb.rawtime(0))
            else:
                # display last lap time
                seen = model.get_value(iter, COL_RFSEEN)
                if len(seen) > 0:
                    if self.start:
                        # show elapsed on the rider!
                        # except perhaps for cx
                        #if len(seen) > 1:
                        # show last lap time
                        #et = seen[-1] - seen[-2]
                        #else:
                        # show elapsed
                        et = seen[-1] - self.start
                    else:
                        et = seen[-1]
                    cr.set_property('text', '[' + et.rawtime(1) + ']')
                    cr.set_property('style', uiutil.STYLE_ITALIC)
                else:
                    cr.set_property('text', '')

    def editstart_cb(self, cell, path, new_text, col=None):
        """Edit start time on rider view."""
        newst = tod.mktod(new_text)
        if newst:
            newst = newst.truncate(0)
        self.riders[path][COL_STOFT] = newst

    def editbunch_cb(self, cell, path, new_text, col=None):
        """Edit bunch time on rider view."""
        new_text = new_text.strip()
        dorecalc = self.edit_mbunch(self.riders[path], new_text)
        if dorecalc:
            self.recalculate()

    def get_leader(self, lr=None):
        """Return the lead rider's bib and bunch time if possible"""
        leader = None
        lbt = None
        if lr is not None and len(self.cats) > 1:
            cs = lr[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            for r in self.riders:
                if r[COL_BIB] == lr[COL_BIB]:
                    _log.debug('Search rider %s is cat %s leader', lr[COL_BIB],
                               rcat)
                    break
                # match against primary cat
                if rcat == self.ridercat(riderdb.primary_cat(r[COL_CAT])):
                    lbt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if lbt is not None:
                        leader = r[COL_BIB]
                        _log.debug('Found cat %s leader: %s', rcat, r[COL_BIB])
                        break
        else:
            lbt = self.vbunch(self.riders[0][COL_CBUNCH],
                              self.riders[0][COL_MBUNCH])
            if lbt is not None:
                leader = self.riders[0][COL_BIB]
            if lr is not None:
                if leader == lr[COL_BIB]:
                    _log.debug('Search rider %s is event leader', lr[COL_BIB])
                    leader = None
        return (leader, lbt)

    def edit_mbunch(self, lr=None, bunch=None):
        """Update manual bunch time and return true if recalc required"""
        # possible values:
        #   - Empty or None:  clear manual bunch entry
        #   - Event time eg:   1h23:45
        #   - Down time from cat leader eg:  +1:23
        #   - Same time as previous rider: s
        #   - Own time (manual time gap): g
        # Assumes model is in bunch order (work in progress)
        ret = False
        if bunch is None or bunch == '':
            # clear values
            lr[COL_MBUNCH] = None
            lr[COL_CBUNCH] = None
            ret = True
            _log.info('Cleared manual bunch time for rider %s', lr[COL_BIB])
        elif lr[COL_PLACE] or lr[COL_COMMENT] == 'otl':
            bunch = bunch.lower()

            # get current bunch for the rider in question
            vbt = self.vbunch(lr[COL_CBUNCH], lr[COL_MBUNCH])

            # get previous and next rider info
            pr = lr.get_previous()
            pt = None
            if pr is not None:
                pt = self.vbunch(pr[COL_CBUNCH], pr[COL_MBUNCH])
            nr = lr.get_next()
            nt = None
            if nr is not None:
                nt = self.vbunch(nr[COL_CBUNCH], nr[COL_MBUNCH])

            if bunch.startswith('s'):
                if pr is not None and pt is not None:
                    ret = True
                    lr[COL_MBUNCH] = pt
                    _log.info('Rider %s assigned same bunch as %s: %s',
                              lr[COL_BIB], pr[COL_BIB], pt.rawtime(0))
                    if nt is not None and nt == vbt:
                        # next rider was same time
                        if nr[COL_MBUNCH] is not None:
                            nr[COL_MBUNCH] = pt
                else:
                    _log.error('No previous rider to set bunch time')
            elif bunch.startswith('g'):
                if lr[COL_RFTIME] is not None:
                    st = tod.ZERO
                    if self.start is not None:
                        st = self.start
                    ret = True
                    lr[COL_MBUNCH] = (lr[COL_RFTIME] - st).truncate(0)
                    _log.info('Rider %s assigned new bunch time: %s',
                              lr[COL_BIB], lr[COL_MBUNCH].rawtime(0))
                    if nt is not None and nt == vbt:
                        # next rider was same time
                        if nr[COL_MBUNCH] is not None:
                            nr[COL_MBUNCH] = lr[COL_MBUNCH]
                else:
                    _log.error('Rider %s does not have arrival time',
                               lr[COL_BIB])
            else:
                # manual override of one bunch time
                nbt = None
                if bunch.startswith('+'):
                    oft = tod.mktod(bunch[1:])
                    if oft is not None:
                        leadrider, leadbunch = self.get_leader(lr)
                        if leadrider is not None and lr[COL_BIB] != leadrider:
                            nbt = leadbunch + oft
                            _log.debug('Using rider %s as cat leader',
                                       leadrider)
                        else:
                            _log.debug('No lead rider, %s down time ignored',
                                       lr[COL_BIB])
                else:
                    nbt = tod.mktod(bunch)

                if nbt is not None:
                    lr[COL_MBUNCH] = nbt.truncate(0)
                    ret = True
                    _log.info('Rider %s bunch time set to: %s', lr[COL_BIB],
                              lr[COL_MBUNCH].rawtime(0))
                    if nt is not None and nt == vbt:
                        # next rider was at same time, preserve orignal bunch
                        nr[COL_MBUNCH] = vbt
                else:
                    _log.warning('Invalid bunch time ignored for rider %s',
                                 lr[COL_BIB])
        else:
            _log.error('Cannot edit bunch time on un-placed rider %s',
                       lr[COL_BIB])
        return ret

    def checkplaces(self, rlist='', dnf=True):
        """Check the proposed places against current event model."""
        ret = True
        placeset = set()
        for no in strops.reformat_biblist(rlist).split():
            if no != 'x':
                # repetition? - already in place set?
                if no in placeset:
                    _log.error('Duplicate in places: %s', no)
                    ret = False
                placeset.add(no)
                # rider in the model?
                lr = self.getrider(no)
                if lr is None:
                    _log.error('Non-starter in places: %s', no)
                    ret = False
                else:
                    # rider still in the event?
                    if not lr[COL_INRACE]:
                        _log.info('DNF/DNS in places: %r', no)
                        if dnf:
                            ret = False
            else:
                # placeholder needs to be filled in later or left off
                _log.info('Placeholder in places')
        return ret

    def recalculate(self):
        """Recalculator"""
        try:
            with self.recalclock:
                self._dorecalc = False
                self._recalc()
        except Exception as e:
            _log.error('%s recalculating result: %s', e.__class__.__name__, e)
            raise

    def rider_in_cat(self, bib, cat):
        """Return True if rider is in nominated category."""
        ret = False
        if cat:
            dbr = self.meet.rdb.get_rider(bib, self.series)
            if dbr is not None:
                ret = dbr.in_cat(cat)
        return ret

    def get_cat_placesr(self, cat):
        """Return a normalised place str for a cat within main places."""
        placestr = self.places
        pgroups = []
        lp = None
        ng = []
        for placegroup in placestr.split():
            cg = []
            for bib in placegroup.split('-'):
                if self.rider_in_cat(bib, cat):
                    cg.append(bib)
            if len(cg) > 0:  # >= one cat rider in this group
                pgroups.append('-'.join(cg))

        ret = ' '.join(pgroups)
        if cat:
            _log.debug('Cat %s finish: %s', cat, ret)
        return ret

    def assign_finish(self):
        """Transfer finish line places into rider model."""
        placestr = self.places
        placeset = set()
        xfer = {}
        idx = 0

        # scan placegroups once
        for placegroup in placestr.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    xfer[bib] = str(curplace)
                    idx += 1
                else:
                    _log.warning('Duplicate in finish places: %s', bib)

        # scan model once
        for r in self.riders:
            bib = r[COL_BIB]
            if bib in xfer:
                if r[COL_INRACE]:
                    r[COL_PLACE] = xfer[bib]
                else:
                    _log.warning('DNF in finish places: %s', bib)
                del (xfer[bib])
                if not xfer:
                    break

        # handle places not yet in model
        if xfer:
            for bib in xfer:
                ri = self.addrider(bib)
                if ri is not None:
                    lr = Gtk.TreeModelRow(self.riders, ri)
                    _log.info('Added non-starter from finish places: %s', bib)
                    lr[COL_PLACE] = xfer[bib]

    def assign_places(self, contest):
        """Transfer points and bonuses into the named contest."""
        src = self.contestmap[contest]['source']
        if src not in self.reserved_sources and src not in self.intermeds:
            _log.info('Invalid inter source %r in contest %r', src, contest)
            return
        countbackwinner = False  # for stage finish only track winner in cb
        category = self.contestmap[contest]['category']
        tally = self.contestmap[contest]['tally']
        bonuses = self.contestmap[contest]['bonuses']
        points = self.contestmap[contest]['points']
        allsrc = self.contestmap[contest]['all_source']
        allpts = 0
        allbonus = tod.ZERO
        if allsrc:
            if len(points) > 0:
                allpts = points[0]
            if len(bonuses) > 0:
                allbonus = bonuses[0]
        placestr = ''
        if src == 'fin':
            placestr = self.places
            if tally in ('sprint', 'crit'):  # really only for sprints/crits
                countbackwinner = True
        elif src == 'reg':
            placestr = self.get_startlist()
        elif src == 'start':
            placestr = self.get_starters()
        elif src in self.catplaces:  # ERROR -> cat climb tally needs type?
            placestr = self.get_cat_placesr(self.catplaces[src])
            countbackwinner = True
        else:
            placestr = self.intermap[src]['places']
        placeset = set()
        idx = 0
        for placegroup in placestr.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    r = self.getrider(bib)
                    if r is None:
                        _log.error('Invalid rider %s ignored in %s places',
                                   bib, contest)
                        break
                    idx += 1
                    if allsrc:  # all listed places get same pts/bonus..
                        if allbonus is not tod.ZERO:
                            if bib in self.bonuses:
                                self.bonuses[bib] += allbonus
                            else:
                                self.bonuses[bib] = allbonus
                        if tally and tally in self.points and allpts != 0:
                            if bib in self.points[tally]:
                                self.points[tally][bib] += allpts
                            else:
                                self.points[tally][bib] = allpts
                                self.pointscb[tally][
                                    bib] = countback.countback()
                            # No countback for all_source entries
                    else:  # points/bonus as per config
                        if len(bonuses) >= curplace:  # bonus is vector
                            if bib in self.bonuses:
                                self.bonuses[bib] += bonuses[curplace - 1]
                            else:
                                self.bonuses[bib] = bonuses[curplace - 1]
                        if tally and tally in self.points:
                            if len(points) >= curplace:  # points vector
                                if bib in self.points[tally]:
                                    self.points[tally][bib] += points[curplace
                                                                      - 1]
                                else:
                                    self.points[tally][bib] = points[curplace -
                                                                     1]
                            if bib not in self.pointscb[tally]:
                                self.pointscb[tally][
                                    bib] = countback.countback()
                            if countbackwinner:  # stage finish
                                if curplace == 1:  # winner only at finish
                                    self.pointscb[tally][bib][0] += 1
                                else:
                                    self.pointscb[tally][bib][curplace] += 1
                            else:  # intermediate/other
                                if tally == 'climb':  # climbs countback on category winners only
                                    if curplace == 1:
                                        self.pointscb[tally][bib][
                                            category] += 1
                                else:
                                    self.pointscb[tally][bib][curplace] += 1
                else:
                    _log.warning('Duplicate in %s places: %s', contest, bib)

    def decode_limit(self, limitstr, elap=None):
        """Decode a limit and finish time into raw bunch time."""
        ret = None
        if limitstr:
            limit = None
            down = False
            if '+' in limitstr:
                down = True
                limitstr = limitstr.replace('+', '')
            if '%' in limitstr:
                down = True
                if elap is not None:
                    try:
                        frac = 0.01 * float(limitstr.replace('%', ''))
                        limit = tod.tod(int(frac * float(elap.as_seconds())))
                    except Exception:
                        pass
            else:  # assume tod without sanity check
                limit = tod.mktod(limitstr)
                if limit is not None:
                    if elap is not None and limit < elap:
                        down = True  # assume a time less than winner is down
                    else:  # assume raw bunch time, ignore elap
                        pass

            # assign limit discovered above, if possible
            if limit is not None:
                if down:
                    if elap is not None:
                        ret = elap + limit  # down time on finish
                        ret = ret.truncate(0)
                else:
                    ret = limit.truncate(0)  # raw bunch time
            if ret is None:
                _log.warning('Unable to decode time limit: %r', limitstr)
        return ret

    def _recalc(self):
        """Internal recalculate function."""
        # if readonly and calc set - skip recalc
        if self.readonly and self.calcset:
            _log.debug('Cached Recalculate')
            return False

        _log.debug('Recalculate model')
        # clear off old places and bonuses
        self.resetplaces()

        # assign places
        self.assign_finish()
        for c in self.contests:
            self.assign_places(c)

        # do rough sort on in, place, laps, rftime, lastpass
        auxtbl = []
        idx = 0
        for r in self.riders:
            rbib = r[COL_BIB]
            rplace = r[COL_PLACE]
            rftime = tod.MAX
            if r[COL_RFTIME] is not None:
                rftime = r[COL_RFTIME]
            rlaps = r[COL_LAPS]
            lastpass = tod.MAX
            if len(r[COL_RFSEEN]) > 0:
                lastpass = r[COL_RFSEEN][-1]
                # in cross scoring, rftime is same as last passing
                if self.etype == 'cross':
                    rftime = lastpass
            if not rplace or not r[COL_INRACE]:
                rplace == ''
            if not r[COL_INRACE]:
                rlaps = 0
                rftime = tod.MAX
                lastpass = tod.MAX
            if self.etype in ('road', 'criterium'):
                # partition into seen and not seen
                if r[COL_INRACE]:
                    if rftime < tod.MAX or lastpass < tod.MAX:
                        rlaps = 999
                    else:
                        rlaps = 0
            auxtbl.append(
                (not r[COL_INRACE], strops.dnfcode_key(rplace), -rlaps, rftime,
                 lastpass, strops.riderno_key(rbib), idx))
            idx += 1
        if len(auxtbl) > 1:
            auxtbl.sort()
            self.riders.reorder([a[6] for a in auxtbl])

        # compute cbunch values on auto time gaps and manual inputs
        # At this point all riders are assumed to be in finish order
        self.maxfinish = tod.ZERO
        racefinish = None
        ft = None  # the finish or first bunch time
        lt = None  # the rftime of last competitor across line
        ll = None  # lap count of previous competitor for cross scoring
        bt = None  # the 'current' bunch time
        if self.start is not None:
            for r in self.riders:
                rcomment = r[COL_COMMENT]
                if r[COL_INRACE] or rcomment == 'otl':
                    rtime = r[COL_RFTIME]
                    if self.etype in ('cross', 'circuit'):
                        if ll is None or ll != r[COL_LAPS]:
                            # invalidate last passing since on a different lap
                            lt = None
                            bt = None
                            ll = r[COL_LAPS]
                    if r[COL_MBUNCH] is not None:
                        bt = r[COL_MBUNCH]  # override with manual bunch
                        r[COL_CBUNCH] = bt
                        if ft is None:
                            ft = bt
                        lt = rtime
                    elif rtime is not None:
                        # establish elapsed, but allow subsequent override
                        if rtime > self.maxfinish:
                            self.maxfinish = rtime
                        et = rtime - self.start

                        # establish bunch time
                        if ft is None and r[COL_RFTIME] is not None:
                            racefinish = r[COL_RFTIME]  # save event finish
                            ft = et.truncate(0)  # compute first time
                            bt = ft
                        else:
                            if lt is not None and (
                                    rtime < lt or rtime - lt < self.gapthresh):
                                # same time
                                pass
                            else:
                                bt = et.truncate(0)

                        # assign and continue
                        r[COL_CBUNCH] = bt
                        lt = rtime
                    else:
                        # empty rftime with non-empty rank implies no time gap
                        if r[COL_PLACE]:
                            r[COL_CBUNCH] = bt  # use current bunch time
                        else:
                            r[COL_CBUNCH] = None

                        # for riders still lapping, extend maxfinish too
                        if len(r[COL_RFSEEN]) > 1:
                            lpass = r[COL_RFSEEN][-1]
                            if lpass is not None and lpass > self.maxfinish:
                                self.maxfinish = lpass

        # if racefinish defined, call set finish
        if racefinish:
            self.set_finish(racefinish)

        # re-sort on in,vbunch (not valid for cross scoring)
        # at this point all finished riders will have valid bunch time
        if self.etype != 'cross':
            auxtbl = []
            idx = 0
            for r in self.riders:
                # aux cols: ind, bib, in, place, vbunch
                rbib = r[COL_BIB]
                rplace = r[COL_PLACE]
                rlaps = r[COL_LAPS]
                rbunch = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                if rbunch is None:
                    rbunch = tod.MAX
                if not r[COL_INRACE]:
                    rplace = r[COL_COMMENT]
                    rlaps = 0
                elif self.etype in ('road', 'criterium'):
                    # group all finished riders on same 'lap'
                    if rbunch < tod.MAX or r[COL_RFTIME] is not None:
                        rlaps = 999
                auxtbl.append((not r[COL_INRACE], strops.dnfcode_key(rplace),
                               -rlaps, rbunch, idx))
                idx += 1
            if len(auxtbl) > 1:
                auxtbl.sort()
                self.riders.reorder([a[4] for a in auxtbl])

        # Scan model to determine racestat and time limits
        if self.timerstat != 'idle':
            limit = None
            if ft is not None and self.timelimit is not None:
                limit = self.decode_limit(self.timelimit, ft)
                if limit is not None:
                    _log.debug('Time limit: %r = %s, +%s', self.timelimit,
                               limit.rawtime(0), (limit - ft).rawtime(0))
                    # and export to announce
                    self.meet.cmd_announce('timelimit', limit.rawtime(0))
            tot = 0
            placed = 0
            handled = 0
            ft = None
            for r in self.riders:
                tot += 1
                if r[COL_INRACE]:
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if ft is None:
                        ft = bt
                    if r[COL_PLACE]:
                        placed += 1
                        handled += 1
                    else:
                        if limit is not None and bt is not None:
                            if bt > limit:
                                r[COL_COMMENT] = 'otl'
                                handled += 1
                            else:  # and clear if not
                                if r[COL_COMMENT] == 'otl':
                                    r[COL_COMMENT] = ''
                else:
                    handled += 1
            if self.timerstat == 'finished' or handled == tot:
                self.racestat = 'final'
            else:
                if placed >= 10 or (placed > 0 and tot < 16):
                    self.racestat = 'provisional'
                else:
                    self.racestat = 'virtual'
        else:
            self.racestat = 'prerace'

        # if final places in view, update text entry
        curact = self.meet.action_model.get_value(
            self.meet.action_combo.get_active_iter(), 0)
        if curact == 'fin':
            self.meet.action_entry.set_text(self.places)
        _log.debug('Event status: %r', self.racestat)
        self.calcset = True
        return False  # allow idle add

    def new_start_trigger(self, rfid):
        """Collect a timer trigger signal and apply it to the model."""
        if self.newstartdlg is not None and self.newstartent is not None:
            et = tod.mktod(self.newstartent.get_text())
            if et is not None:
                dlg = self.newstartdlg
                self.newstartdlg = None
                wasrunning = self.timerstat in ('running', 'armfinish')
                st = rfid - et
                self.set_start(st)
                if wasrunning:
                    # flag a recalculate
                    self._dorecalc = True
                else:
                    self.resetcatonlaps()
                    if self.etype in ('criterium', 'circuit', 'cross'):
                        GLib.idle_add(self.armlap)
                dlg.response(1)
            else:
                _log.warning('Invalid elapsed time: Start not updated')
        return False

    def new_start_trig(self, button, entry=None):
        """Use the current time to update start offset."""
        self.meet._timercb(tod.now())

    def verify_timent(self, entry, data=None):
        et = tod.mktod(entry.get_text())
        if et is not None:
            entry.set_text(et.rawtime())
        else:
            _log.info('Invalid elapsed time')

    def elapsed_dlg(self, addriders=''):
        """Run a 'new start' dialog."""
        if self.timerstat == 'armstart':
            _log.error('Start is armed, unarm to add new start time')
            return

        b = uiutil.builder('new_start.ui')
        dlg = b.get_object('newstart')
        try:
            dlg.set_transient_for(self.meet.window)
            self.newstartdlg = dlg

            timent = b.get_object('time_entry')
            timent.set_text('0.0')
            self.newstartent = timent
            timent.connect('activate', self.verify_timent)

            self.meet.timercb = self.new_start_trigger
            b.get_object('now_button').connect('button-press-event',
                                               self.new_start_trig)

            response = dlg.run()
            self.newstartdlg = None
            if response == 1:  # id 1 set in glade for "Apply"
                _log.info('Start time updated: %s', self.start.rawtime(2))
            else:
                _log.info('Set elapsed time cancelled')
        except Exception as e:
            _log.debug('%s setting elapsed time: %s', e.__class__.__name__, e)
        finally:
            self.meet.timercb = self.timertrig
            dlg.destroy()

    def treeview_button_press(self, treeview, event):
        """Set callback for mouse press on model view."""
        if event.button == 3:
            pathinfo = treeview.get_path_at_pos(int(event.x), int(event.y))
            if pathinfo is not None:
                path, col, cellx, celly = pathinfo
                treeview.grab_focus()
                treeview.set_cursor(path, col, False)
                self.context_menu.popup_at_pointer(None)
                return True
        return False

    def chg_dst_ent(self, entry, data):
        bib = entry.get_text()
        sbib = data[2]
        nv = '[Invalid Rider]'
        rv = ''
        if sbib != bib:
            i = self.getiter(bib)
            if i is not None:
                nv = self.riders.get_value(i, COL_NAMESTR)
                rv = self.getbunch_iter(i)
        data[0].set_text(nv)
        data[1].set_text(rv)

    def placeswap(self, src, dst):
        """Swap the src and dst riders if they appear in places."""
        _log.debug('Places before swap: %r', self.places)
        newplaces = []
        for placegroup in self.places.split():
            gv = placegroup.split('-')
            sind = None
            try:
                sind = gv.index(src)
            except Exception:
                pass
            dind = None
            try:
                dind = gv.index(dst)
            except Exception:
                pass
            if sind is not None:
                gv[sind] = dst
            if dind is not None:
                gv[dind] = src
            newplaces.append('-'.join(gv))
        self.places = ' '.join(newplaces)
        _log.debug('Places after swap: %r', self.places)

    def rms_context_down_activate_cb(self, menuitem, data=None):
        """Assign a finish time based on laps down from cat leader."""
        sel = self.view.get_selection().get_selected()
        bib = None
        if sel is not None:
            i = sel[1]
            r = Gtk.TreeModelRow(self.riders, i)
            if len(r[COL_RFSEEN]) > 0:
                self.lapsdown(r, r[COL_RFSEEN][-1])
            else:
                _log.info('No passings to use for laps down')
        if sel is None:
            _log.info('Unable to set empty rider selection')

    def lapsdown(self, lr, passing=None):
        # determine rider's result cat
        cs = lr[COL_CAT]
        rcat = self.ridercat(riderdb.primary_cat(cs))
        bt = None
        ldr = None
        # scan rider list for cat leader
        for r in self.riders:
            if rcat == '' or rcat in r[COL_CAT]:
                bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                if bt is not None:
                    st = self.getstart(r)
                    if st is not None:
                        bt = bt - st
                    # rider has a finish bunch
                    ldr = r
                    break

        # fetch start stime offset
        st = self.start
        sof = tod.ZERO  # check this
        if r[COL_STOFT] is not None:
            sof = r[COL_STOFT]
        elif rcat and rcat in self.catstarts:
            if self.catstarts[rcat] is not None:
                sof = self.catstarts[rcat]

        # determine elapsed time
        et = passing - (st + sof)

        if ldr is not None:
            # compare laps
            if ldr[COL_LAPS] > 0 and ldr[COL_LAPS] > lr[COL_LAPS]:
                lavg = tod.tod(et.timeval / lr[COL_LAPS])
                deficit = ldr[COL_LAPS] - lr[COL_LAPS]
                lxtra = tod.tod(lavg.timeval * deficit)
                lr[COL_RFTIME] = passing + lxtra
                _log.debug(
                    'Leader %r: %d laps, Rider %r: %d laps, avg=%s, deficit=%d, xtra=%s, pass=%s, fin=%s, ',
                    ldr[COL_BIB], ldr[COL_LAPS], lr[COL_BIB], lr[COL_LAPS],
                    lavg.rawtime(1), deficit, lxtra.rawtime(1),
                    passing.rawtime(1), lr[COL_RFTIME].rawtime(1))

                self._dorecalc = True
            else:
                _log.warning(
                    'Leader %r on lap %r, rider %r on lap %r - unchanged',
                    ldr[COL_BIB], ldr[COL_LAPS], lr[COL_BIB], lr[COL_LAPS])
        else:
            _log.warning('No leader found for cat %r, %s unchanged', rcat,
                         lr[COL_BIB])

    def rms_context_swap_activate_cb(self, menuitem, data=None):
        """Swap data to/from another rider."""
        sel = self.view.get_selection().get_selected()
        if sel is None:
            _log.info('Unable to swap empty rider selection')
            return
        srcbib = self.riders.get_value(sel[1], COL_BIB)
        spcat = riderdb.primary_cat(self.riders.get_value(sel[1], COL_CAT))
        spare = spcat == 'SPARE'
        srcname = self.riders.get_value(sel[1], COL_NAMESTR)
        srcinfo = self.getbunch_iter(sel[1])
        b = uiutil.builder('swap_rider.ui')
        dlg = b.get_object('swap')
        dlg.set_transient_for(self.meet.window)
        src_ent = b.get_object('source_entry')
        src_ent.set_text(srcbib)
        src_lbl = b.get_object('source_label')
        src_lbl.set_text(srcname)
        src_res = b.get_object('source_result')
        src_res.set_text(srcinfo)
        dst_ent = b.get_object('dest_entry')
        dst_lbl = b.get_object('dest_label')
        dst_result = b.get_object('dest_result')
        dst_ent.connect('changed', self.chg_dst_ent,
                        (dst_lbl, dst_result, srcbib))
        ret = dlg.run()
        if ret == 1:
            dstbib = dst_ent.get_text()
            if dstbib != srcbib:
                dr = self.getrider(dstbib)
                if dr is not None:
                    self.placeswap(dstbib, srcbib)
                    sr = self.getrider(srcbib)
                    for col in (COL_COMMENT, COL_INRACE, COL_PLACE, COL_LAPS,
                                COL_RFTIME, COL_CBUNCH, COL_MBUNCH,
                                COL_RFSEEN):
                        tv = dr[col]
                        dr[col] = sr[col]
                        sr[col] = tv
                    _log.info('Swap riders %s <=> %s', srcbib, dstbib)
                    # If srcrider was a spare bike, remove the spare and patch
                    if spare:
                        ac = [t for t in sr[COL_RFSEEN]]
                        ac.extend(dr[COL_RFSEEN])
                        dr[COL_RFSEEN] = [t for t in sorted(ac)]
                        dr[COL_LAPS] = len(dr[COL_RFSEEN])
                        self.delrider(srcbib)
                        _log.debug('Spare bike %s removed', srcbib)
                    # If dstrider is a spare bike, leave it in place
                    self.recalculate()
                else:
                    _log.error('Invalid rider swap %s <=> %s', srcbib, dstbib)
            else:
                _log.info('Swap to same rider ignored')
        else:
            _log.info('Swap rider cancelled')
        dlg.destroy()

    def rms_context_edit_activate_cb(self, menuitem, data=None):
        """Edit rider start/finish/etc."""
        sel = self.view.get_selection().get_selected()
        if sel is None:
            return False

        lr = Gtk.TreeModelRow(self.riders, sel[1])
        st = lr[COL_STOFT]
        stextra = ''
        if not st:
            # check for a category start time
            cs = lr[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if rcat in self.catstarts and self.catstarts[rcat] is not None:
                st = self.catstarts[rcat]
                stextra = '[Cat start: %s]' % (rcat)

        lastpass = None
        if len(lr[COL_RFSEEN]) > 0:
            lastpass = lr[COL_RFSEEN][-1]
        placestr = None
        if lr[COL_COMMENT]:
            placestr = lr[COL_COMMENT]
        placeopts = {
            '': 'Unclassified',
            'dns': 'Did not start',
            'otl': 'Outside time limit',
            'dnf': 'Did not finish',
            'dsq': 'Disqualified',
        }
        if lr[COL_PLACE]:
            placeopts['plc'] = 'Placed ' + strops.rank2ord(lr[COL_PLACE])
        if lr[COL_INRACE]:
            if lr[COL_PLACE]:
                placestr = 'plc'
        if placestr is not None and placestr not in placeopts:
            placeopts[placestr] = placestr
        manbunch = None
        if lr[COL_MBUNCH] is not None:
            manbunch = lr[COL_MBUNCH].rawtime(0)
        sections = {
            'result': {
                'object': None,
                'title': 'result',
                'schema': {
                    'title': {
                        'prompt': lr[COL_BIB] + ' ' + lr[COL_NAMESTR],
                        'control': 'section',
                    },
                    'seed': {
                        'prompt': 'Seed:',
                        'hint': 'Seeding number for startlists',
                        'control': 'short',
                        'type': 'int',
                        'value': lr[COL_SEED],
                        'index': COL_SEED,
                    },
                    'class': {
                        'prompt': 'Classification:',
                        'hint': 'Rider classification for event',
                        'control': 'choice',
                        'value': placestr,
                        'options': placeopts,
                        'default': '',
                    },
                    'start': {
                        'prompt': 'Start Offset:',
                        'hint': 'Start offset',
                        'type': 'tod',
                        'places': 0,
                        'control': 'short',
                        'value': st,
                        'subtext': stextra,
                        'index': COL_STOFT,
                    },
                    'laps': {
                        'prompt': 'Laps:',
                        'hint': 'Rider lap count',
                        'control': 'short',
                        'type': 'int',
                        'value': lr[COL_LAPS],
                        'index': COL_LAPS,
                    },
                    'lpass': {
                        'prompt': 'Last Pass:',
                        'hint': 'Time last seen at finish line',
                        'type': 'tod',
                        'places': 4,
                        'readonly': 'true',
                        'control': 'short',
                        'value': lastpass,
                    },
                    'rftime': {
                        'prompt': 'Finish:',
                        'hint': 'Time of arrival at event finish',
                        'type': 'tod',
                        'places': 4,
                        'value': lr[COL_RFTIME],
                        'nowbut': True,
                        'control': 'short',
                        'subtext': 'Set finish time to now',
                        'index': COL_RFTIME,
                    },
                    'cbunch': {
                        'prompt': 'Bunch:',
                        'hint': 'Computed bunch time',
                        'type': 'tod',
                        'places': 0,
                        'value': lr[COL_CBUNCH],
                        'control': 'short',
                        'readonly': 'true',
                    },
                    'mbunch': {
                        'prompt': 'Man Bunch:',
                        'hint': 'Override computed bunch time',
                        'places': 0,
                        'value': manbunch,
                        'control': 'short',
                    },
                    'bonus': {
                        'prompt': 'Stage Bonus:',
                        'hint': 'Additional stage bonus time',
                        'type': 'tod',
                        'places': 0,
                        'value': lr[COL_BONUS],
                        'control': 'short',
                        'index': COL_BONUS,
                    },
                    'penalty': {
                        'prompt': 'Stage Penalty:',
                        'hint': 'Additional stage penalty time',
                        'type': 'tod',
                        'places': 0,
                        'value': lr[COL_PENALTY],
                        'control': 'short',
                        'index': COL_PENALTY,
                    },
                },
            },
        }
        res = uiutil.options_dlg(window=self.meet.window,
                                 title='Edit times',
                                 sections=sections)
        changed = False
        for option in res['result']:
            if res['result'][option][0]:
                changed = True
                if 'index' in sections['result']['schema'][option]:
                    index = sections['result']['schema'][option]['index']
                    lr[index] = res['result'][option][2]
                    _log.debug('Updated %s to: %r', option,
                               res['result'][option][2])
                elif option == 'class':
                    newclass = res['result'][option][2]
                    if newclass is None:
                        newclass = ''
                    if newclass in ('dns', 'otl', 'dnf', 'dsq'):
                        lr[COL_INRACE] = False
                    else:
                        lr[COL_INRACE] = True
                    if newclass != 'plc':
                        lr[COL_COMMENT] = newclass
                        if res['result'][option][1] == 'plc':
                            self.clear_place(lr[COL_BIB])
                elif option == 'mbunch':
                    self.edit_mbunch(lr, res['result'][option][2])
                else:
                    _log.debug('Unknown option %r changed', option)
        if changed:
            self.recalculate()

    def rms_context_chg_activate_cb(self, menuitem, data=None):
        """Update selected rider from event."""
        change = menuitem.get_label().lower()
        sel = self.view.get_selection().get_selected()
        bib = None
        if sel is not None:
            i = sel[1]
            selbib = self.riders.get_value(i, COL_BIB)
            if change == 'delete':
                _log.info('Delete rider: %s', selbib)
                self.delrider(selbib)
            elif change == 'clear finish':
                _log.info('Clear rider %s finish time', selbib)
                self.riders.set_value(i, COL_RFTIME, None)
                self.riders.set_value(i, COL_MBUNCH, None)
                self.recalculate()
            elif change == 'refinish':
                splits = self.riders.get_value(i, COL_RFSEEN)
                if splits is not None and len(splits) > 0:
                    nf = splits[-1]
                    _log.info(
                        'Set raw finish for rider %s to last passing: %s',
                        selbib, nf.rawtime(2))
                    self.riders.set_value(i, COL_RFTIME, nf)
                    self.recalculate()
            elif change in _DNFLABELS:
                self.dnfriders(selbib, _DNFLABELS[change])
            elif change == 'return':
                self.retriders(selbib)
            elif change == 'passing':
                self.manpassing(selbib)
            else:
                _log.info('Unknown rider change %r ignored', change)

    def __init__(self, meet, etype, ui=True):
        self.meet = meet
        self.etype = etype
        self.series = ''
        self.configfile = 'event.json'
        self.readonly = not ui
        rstr = ''
        if self.readonly:
            rstr = 'readonly '
        _log.debug('Init %s event', rstr)

        self.recalclock = threading.Lock()
        self._dorecalc = False

        # event run time attributes
        self.calcset = False
        self.start = None
        self.finish = None
        self.altfinish = None
        self.maxfinish = None
        self.showdowntimes = True
        self.winopen = True
        self.timerstat = 'idle'
        self.racestat = 'prerace'
        self.places = ''
        self.laptimes = []
        self.decisions = []
        self.hidecols = []
        self.cats = []
        self.passingsource = None  # loop id no for valid passing
        self.autofinish = False  # true if finish is det by target
        self.catlaps = {}  # cache of cat lap counts
        self.catstarts = {}  # cache of cat start times
        self.catplaces = {}
        self.autocats = False
        self.autostartlist = None
        self.bonuses = {}
        self.points = {}
        self.pointscb = {}
        self.dofastestlap = False
        self.hcpcatres = False
        self.autoexport = False
        self.timelimit = None
        self.passlabels = {}  # sector labels for mult passings
        self.catonlap = {}  # onlap per category
        self.clubmode = False
        self.allowspares = False
        self.gapthresh = GAPTHRESH  # time gap to set new time
        # NOTE: .12 usually added to account
        # for front wheel measurements
        self.curlap = -1
        self.onlap = 1
        self.totlaps = None
        self.lapstart = None
        self.lapfin = None
        self.minlap = MINPASSTIME  # minimum lap/elap time if relevant

        # stage intermediates
        self.reserved_sources = RESERVED_SOURCES
        self.intermeds = []  # sorted list of intermediate keys
        self.intermap = {}  # map of intermediate keys to results
        self.contests = []  # sorted list of contests
        self.contestmap = {}  # map of contest keys
        self.tallys = []  # sorted list of points tallys
        self.tallymap = {}  # map of tally keys

        # announce cache
        self.scratch_map = {}
        self.scratch_ord = []
        self.live_announce = True

        # new start dialog
        self.newstartent = None
        self.newstartdlg = None

        self.ridernos = set()
        self.riders = Gtk.ListStore(
            str,  # gobject.TYPE_STRING,  # BIB = 0
            str,  # gobject.TYPE_STRING,  # NAMESTR = 1
            str,  # gobject.TYPE_STRING,  # SHORTNAME = 2
            str,  # gobject.TYPE_STRING,  # CAT = 3
            str,  # gobject.TYPE_STRING,  # COMMENT = 4
            bool,  #gobject.TYPE_BOOLEAN,  # INRACE = 5
            str,  # gobject.TYPE_STRING,  # PLACE = 6
            int,  # gobject.TYPE_INT,  # LAP COUNT = 7
            int,  # gobject.TYPE_INT,  # SEED = 8
            object,  # gobject.TYPE_PYOBJECT,  # RFTIME = 9
            object,  # gobject.TYPE_PYOBJECT,  # CBUNCH = 10
            object,  # gobject.TYPE_PYOBJECT,  # MBUNCH =11 
            object,  # gobject.TYPE_PYOBJECT,  # STOFT = 12
            object,  # gobject.TYPE_PYOBJECT,  # BONUS = 13
            object,  # gobject.TYPE_PYOBJECT,  # PENALTY = 14
            object,  # gobject.TYPE_PYOBJECT  # RFSEEN = 15
        )

        b = uiutil.builder('rms.ui')
        self.frame = b.get_object('event_vbox')

        # meta info pane
        self.shortname = None
        self.title_namestr = b.get_object('title_namestr')
        self.set_titlestr()
        self.elaplbl = b.get_object('time_lbl')
        self.lapentry = b.get_object('lapentry')
        self.totlapentry = b.get_object('totlapentry')

        # Result pane
        t = Gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(True)
        t.set_rules_hint(True)

        self.context_menu = None
        if ui:
            self.frame.connect('destroy', self.shutdown)
            uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
            uiutil.mkviewcoltxt(t,
                                'Rider',
                                COL_NAMESTR,
                                expand=True,
                                maxwidth=500,
                                cb=self.editname_cb)
            uiutil.mkviewcoltxt(t, 'Cat', COL_CAT, cb=self.editcat_cb)
            uiutil.mkviewcoltxt(t, 'Com', COL_COMMENT, cb=self.editcol_cb)
            uiutil.mkviewcolbool(t, 'In', COL_INRACE, width=50)
            uiutil.mkviewcoltxt(t,
                                'Lap',
                                COL_LAPS,
                                width=40,
                                cb=self.editlap_cb)
            uiutil.mkviewcoltxt(t,
                                'Seed',
                                COL_SEED,
                                width=40,
                                cb=self.editseed_cb)
            uiutil.mkviewcoltod(t,
                                'Start',
                                cb=self.showstart_cb,
                                width=50,
                                editcb=self.editstart_cb)
            uiutil.mkviewcoltod(t,
                                'Time',
                                cb=self.showbunch_cb,
                                editcb=self.editbunch_cb,
                                width=50)
            uiutil.mkviewcoltxt(t, 'Place', COL_PLACE, calign=0.5, width=50)
            t.show()
            b.get_object('event_result_win').add(t)
            self.context_menu = b.get_object('rms_context')
            self.view.connect('button_press_event', self.treeview_button_press)
            b.connect_signals(self)
            self.meet.timercb = self.timertrig
            self.meet.alttimercb = self.alttimertrig
