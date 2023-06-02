# SPDX-License-Identifier: MIT
"""Individual road time trial handler for roadmeet."""

import os
import gi
import logging

gi.require_version("GLib", "2.0")
from gi.repository import GLib

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

import metarace
from metarace import tod
from metarace import eventdb
from metarace import riderdb
from metarace import strops
from metarace import countback
from metarace import report
from metarace import jsonconfig
from . import uiutil

_log = logging.getLogger('metarace.irtt')
_log.setLevel(logging.DEBUG)

# rider commands
RIDER_COMMANDS_ORD = [
    'add', 'del', 'que', 'onc', 'dns', 'otl', 'dnf', 'dsq', 'com', ''
]
RIDER_COMMANDS = {
    'dns': 'Did not start',
    'dnf': 'Did not finish',
    'add': 'Add starters',
    'del': 'Remove starters',
    'que': 'Query riders',
    'com': 'Add comment',
    'otl': 'Outside time limit',
    'dsq': 'Disqualify',
    'onc': 'Riders on course',
    '': '',
}

RESERVED_SOURCES = [
    'fin',  # finished stage
    'reg',  # registered to stage
    'start'
]  # started stage

DNFCODES = ['otl', 'dsq', 'dnf', 'dns']
STARTFUDGE = tod.tod(30)
ARRIVALTIMEOUT = tod.tod('2:30')

# startlist model columns
COL_BIB = 0
COL_SERIES = 1
COL_NAMESTR = 2
COL_CAT = 3
COL_COMMENT = 4
COL_WALLSTART = 5
COL_TODSTART = 6
COL_TODFINISH = 7
COL_TODPENALTY = 8
COL_PLACE = 9
COL_SHORTNAME = 10
COL_INTERA = 11
COL_INTERB = 12
COL_INTERC = 13
COL_INTERD = 14
COL_INTERE = 15
COL_LASTSEEN = 16
COL_ETA = 17
COL_PASS = 18
COL_BONUS = 19
COL_PENALTY = 20
_START_MATCH_THRESH = tod.tod('5.0')
_FINISH_MATCH_THRESH = tod.tod('0.200')

# scb function key mappings
key_startlist = 'F6'  # clear scratchpad (FIX)
key_results = 'F4'  # recalc/show results in scratchpad
key_starters = 'F3'  # show next few starters in scratchpad

# timing function key mappings
key_armsync = 'F1'  # arm for clock sync start
key_armstart = 'F5'  # arm for start impulse
key_armfinish = 'F9'  # arm for finish impulse
key_raceover = 'F10'  # flag race completion/not provisional

# extended function key mappings
key_reset = 'F5'  # + ctrl for clear/abort
key_falsestart = 'F6'  # + ctrl for false start
key_abort_start = 'F7'  # + ctrl abort A
key_abort_finish = 'F8'  # + ctrl abort B

# config version string
EVENT_ID = 'roadtt-3.1'


def jsob(inmap):
    """Return a json'able map."""
    ret = None
    if inmap is not None:
        ret = {}
        for key in inmap:
            if key in ['minelap', 'maxelap']:
                ret[key] = inmap[key].rawtime()
            else:
                ret[key] = inmap[key]
    return ret


def unjsob(inmap):
    """Un-jsob the provided map."""
    ret = None
    if inmap is not None:
        ret = {}
        for key in inmap:
            if key in ['minelap', 'maxelap']:
                ret[key] = tod.mktod(inmap[key])
            else:
                ret[key] = inmap[key]
    return ret


class irtt:
    """Data handling for road time trial."""

    def reset_clear(self):
        """Return event to idle and remove all results"""
        _log.debug('Reset')
        self.startpasses.clear()
        self.finishpasses.clear()
        self.resetall()
        i = self.riders.get_iter_first()
        while i is not None:
            self.riders.set_value(i, COL_COMMENT, '')
            self.riders.set_value(i, COL_PASS, 0)
            self.riders.set_value(i, COL_LASTSEEN, None)
            self.settimes(i, doplaces=False)
            i = self.riders.iter_next(i)
        for cat in self.cats:
            self.results[cat].clear()
            self.inters[COL_INTERA][cat].clear()
            self.inters[COL_INTERB][cat].clear()
            self.inters[COL_INTERC][cat].clear()
            self.inters[COL_INTERD][cat].clear()
            self.inters[COL_INTERE][cat].clear()
        self.placexfer()

    def key_event(self, widget, event):
        """Race window key press handler."""
        if event.type == Gdk.EventType.KEY_PRESS:
            key = Gdk.keyval_name(event.keyval) or 'None'
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                if key == key_reset:  # override ctrl+f5
                    if uiutil.questiondlg(
                            self.meet.window, 'Reset event to idle?',
                            'Note: All result and timing data will be cleared.'
                    ):
                        self.reset_clear()
                    return True
                elif key == key_falsestart:  # false start both lanes
                    return True
                elif key == key_abort_start:  # abort start line
                    return True
                elif key == key_abort_finish:  # abort finish line
                    return True
            if key[0] == 'F':
                if key == key_armstart:
                    self.armstart()
                    return True
                elif key == key_armfinish:
                    self.armfinish()
                    return True
                elif key == key_startlist:
                    self.meet.cmd_announce('clear', 'all')
                    self.doannounce = True
                    return True
                elif key == key_raceover:
                    self.set_finished()
                    return True
                elif key == key_results:
                    return True
        return False

    def resetall(self):
        """Reset timers."""
        self.fl.toidle()
        self.fl.disable()

    def set_finished(self):
        """Update event status to finished."""
        if self.timerstat == 'finished':
            self.timerstat = 'running'
            self.meet.stat_but.buttonchg(uiutil.bg_armstart, 'Running')
            self.meet.stat_but.set_sensitive(True)
        else:
            self.timerstat = 'finished'
            self.meet.stat_but.buttonchg(uiutil.bg_none, 'Finished')
            self.meet.stat_but.set_sensitive(False)
            self.hidetimers = True
            self.timerframe.hide()

    def armfinish(self):
        if self.timerstat == 'running':
            if self.fl.getstatus() != 'finish' and self.fl.getstatus(
            ) != 'armfin':
                self.fl.toarmfin()
            else:
                self.fl.toidle()
                self.announce_rider()

    def armstart(self):
        if self.timerstat == 'idle':
            _log.info('Armed for timing sync')
            self.timerstat = 'armstart'
        elif self.timerstat == 'armstart':
            self.resetall()
        elif self.timerstat == 'running':
            if self.sl.getstatus() in ['armstart', 'running']:
                self.sl.toidle()
            elif self.sl.getstatus() != 'running':
                self.sl.toarmstart()

    def delayed_announce(self):
        """Re-announce all riders from the nominated category."""
        self.meet.cmd_announce('clear', 'all')
        heading = ''
        if self.timerstat == 'finished':
            heading = ': Result'
        else:
            if self.racestat == 'prerace':
                heading = ''  # anything better?
            else:
                heading = ': Standings'
        self.meet.cmd_announce('title',
                               self.title_namestr.get_text() + heading)
        self.meet.cmd_announce('finstr', self.meet.get_short_name())
        cat = self.ridercat(self.curcat)
        for t in self.results[cat]:
            r = self.getiter(t[0].refid, t[0].index)
            if r is not None:
                et = self.getelapsed(r)
                bib = t[0].refid
                rank = self.riders.get_value(r, COL_PLACE)
                cat = self.riders.get_value(r, COL_CAT)
                namestr = self.riders.get_value(r, COL_NAMESTR)
                self.meet.rider_announce(
                    [rank, bib, namestr, cat,
                     et.rawtime(2)])
        arrivalsec = self.arrival_report(0)  # fetch all arrivals
        if len(arrivalsec) > 0:
            arrivals = arrivalsec[0].lines
            for a in arrivals:
                # announce arrival
                pass
        return False

    def edit_event_properties(self, window, data=None):
        """Edit event specifics."""
        _log.warning('Edit event properties not implemented')

    def wallstartstr(self, col, cr, model, iter, data=None):
        """Format start time into text for listview."""
        st = model.get_value(iter, COL_TODSTART)
        if st is not None:
            cr.set_property('text', st.timestr(2))  # time from tapeswitch
            cr.set_property('style', uiutil.STYLE_NORMAL)
        else:
            cr.set_property('style', uiutil.STYLE_OBLIQUE)
            wt = model.get_value(iter, COL_WALLSTART)
            if wt is not None:
                cr.set_property('text', wt.timestr(0))  # adv start
            else:
                cr.set_property('text', '')  # no info on start time

    def announce_rider(self,
                       place='',
                       bib='',
                       namestr='',
                       shortname='',
                       cat='',
                       rt=None,
                       et=None):
        """Emit a finishing rider to announce."""
        rts = ''
        if et is not None:
            rts = et.rawtime(2)
        elif rt is not None:
            rts = rt.rawtime(0)
        # Announce rider
        ##self.meet.scb.add_rider([place, bib, shortname, cat, rts], 'finpanel')
        ##self.meet.scb.add_rider([place, bib, namestr, cat, rts], 'finish')

    def geteta(self, iter):
        """Return a best guess rider's ET."""
        ret = self.getelapsed(iter)
        if ret is None:
            # scan each inter from farthest to nearest
            for ipt in [
                    COL_INTERE, COL_INTERD, COL_INTERC, COL_INTERB, COL_INTERA
            ]:
                if ipt in self.ischem and self.ischem[ipt] is not None:
                    dist = self.ischem[ipt]['dist']
                    inter = self.riders.get_value(iter, ipt)
                    if inter is not None and dist is not None:
                        totdist = 1000.0 * self.meet.distance
                        st = self.riders.get_value(iter, COL_TODSTART)
                        if st is None:  # defer to start time
                            st = self.riders.get_value(iter, COL_WALLSTART)
                        if st is not None:  # still none is error
                            et = inter - st
                            spd = (1000.0 * dist) / float(et.timeval)
                            ret = tod.tod(str(totdist / spd))
                            self.riders.set_value(iter, COL_PASS, int(dist))
                            break
        return ret

    def getelapsed(self, iter, runtime=False):
        """Return a tod elapsed time."""
        ret = None
        ft = self.riders.get_value(iter, COL_TODFINISH)
        if ft is not None:
            st = self.riders.get_value(iter, COL_TODSTART)
            if st is None:  # defer to start time
                st = self.riders.get_value(iter, COL_WALLSTART)
            if st is not None:  # still none is error
                pt = self.riders.get_value(iter, COL_TODPENALTY)
                # penalties are added into stage result - for consistency
                ret = (ft - st) + pt
        elif runtime:
            st = self.riders.get_value(iter, COL_TODSTART)
            if st is None:  # defer to start time
                st = self.riders.get_value(iter, COL_WALLSTART)
            if st is not None:  # still none is error
                ret = tod.now() - st  # runtime increases!
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

    def checkplaces(self, rlist='', dnf=True):
        """Check the proposed places against current race model."""
        ret = True
        placeset = set()
        for no in strops.reformat_bibserlist(rlist).split():
            if no != 'x':
                # repetition? - already in place set?
                if no in placeset:
                    _log.error('Duplicate no in places: %r', no)
                    ret = False
                placeset.add(no)
                # rider in the model?
                b, s = strops.bibstr2bibser(no)
                lr = self.getrider(b, s)
                if lr is None:
                    _log.error('Non-starter in places: %r', no)
                    ret = False
                else:
                    # rider still in the race?
                    if lr[COL_COMMENT]:
                        _log.warning('DNS/DNF rider in places: %r', no)
                        if dnf:
                            ret = False
            else:
                # placeholder needs to be filled in later or left off
                _log.info('Placeholder in places')
        return ret

    def race_ctrl(self, acode='', rlist=''):
        """Apply the selected action to the provided bib list."""
        if acode in self.intermeds:
            rlist = strops.reformat_bibserplacelist(rlist)
            if self.checkplaces(rlist, dnf=False):
                self.intermap[acode]['places'] = rlist
                self.placexfer()
                _log.info('Intermediate %r == %r', acode, rlist)
            else:
                _log.error('Intermediate %r not updated', acode)
                return False
        elif acode == 'que':
            _log.debug('Query rider not implemented - reannounce ridercat')
            self.curcat = self.ridercat(rlist.strip())
            self.doannounce = True
        elif acode == 'del':
            rlist = strops.reformat_bibserlist(rlist)
            for bibstr in rlist.split():
                bib, ser = strops.bibstr2bibser(bibstr)
                self.delrider(bib, ser)
            return True
        elif acode == 'add':
            _log.info('Add starter deprecated: Use startlist import')
            rlist = strops.reformat_bibserlist(rlist)
            for bibstr in rlist.split():
                bib, ser = strops.bibstr2bibser(bibstr)
                self.addrider(bib, ser)
            return True
        elif acode == 'onc':
            _log.info('on-course ignored')
            return True
        elif acode == 'dnf':
            self.dnfriders(strops.reformat_bibserlist(rlist))
            return True
        elif acode == 'dsq':
            self.dnfriders(strops.reformat_bibserlist(rlist), 'dsq')
            return True
        elif acode == 'otl':
            self.dnfriders(strops.reformat_bibserlist(rlist), 'otl')
            return True
        elif acode == 'dns':
            self.dnfriders(strops.reformat_bibserlist(rlist), 'dns')
            return True
        elif acode == 'com':
            self.add_comment(rlist)
            return True
        else:
            _log.error('Ignoring invalid action %r', acode)
        return False

    def add_comment(self, comment=''):
        """Append a commissaires comment."""
        self.comment.append(comment.strip())
        _log.info('Added comment: %r', comment)

    def elapstr(self, col, cr, model, iter, data=None):
        """Format elapsed time into text for listview."""
        ft = model.get_value(iter, COL_TODFINISH)
        if ft is not None:
            st = model.get_value(iter, COL_TODSTART)
            if st is None:  # defer to wall start time
                st = model.get_value(iter, COL_WALLSTART)
                cr.set_property('style', uiutil.STYLE_OBLIQUE)
            else:
                cr.set_property('style', uiutil.STYLE_NORMAL)
            et = self.getelapsed(iter)
            if et is not None:
                cr.set_property('text', et.timestr(2))
            else:
                cr.set_property('text', '[ERR]')
        else:
            cr.set_property('text', '')

    def loadcats(self, cats=[]):
        self.cats = []  # clear old cat list
        if 'AUTO' in cats:  # ignore any others and re-load from rdb
            self.cats = self.meet.rdb.listcats()
            self.autocats = True
        else:
            self.autocats = False
            for cat in cats:
                if cat != '':
                    cat = cat.upper()
                    if cat not in ['CAT', 'SPARE', 'TEAM']:
                        self.cats.append(cat)
                    else:
                        _log.warning('Invalid result category: %r', cat)
        self.cats.append('')  # always include one empty cat
        self.catlaps = {}
        for cat in self.cats:
            self.results[cat] = tod.todlist(cat)
            self.inters[COL_INTERA][cat] = tod.todlist(cat)
            self.inters[COL_INTERB][cat] = tod.todlist(cat)
            self.inters[COL_INTERC][cat] = tod.todlist(cat)
            self.inters[COL_INTERD][cat] = tod.todlist(cat)
            self.inters[COL_INTERE][cat] = tod.todlist(cat)
            lt = self.finishpass
            dbr = self.meet.rdb.getrider(cat, 'cat')
            if dbr is not None:
                clt = strops.confopt_posint(
                    self.meet.rdb.getvalue(dbr, riderdb.COL_CAT), None)
                if clt is not None:
                    # override lap count from category
                    lt = clt
            self.catlaps[cat] = lt
            _log.debug('Set category %r pass count to: %r', cat, lt)
        _log.debug('Result category list updated: %r', self.cats)

    def loadconfig(self):
        """Load race config from disk."""
        self.riders.clear()
        self.results = {'': tod.todlist('UNCAT')}
        self.cats = []

        cr = jsonconfig.config({
            'irtt': {
                'startlist': '',
                'id': EVENT_ID,
                'start': '0',
                'comment': [],
                'categories': [],
                'arrivalcount': 4,
                'lstart': '0',
                'startgap': '1:00',
                'precision': 1,
                'autoexport': False,
                'intermeds': [],
                'contests': [],
                'minelap': STARTFUDGE,
                'sloppystart': False,
                'sloppyimpulse': False,
                'startdelay': None,
                'startloop': None,
                'starttrig': None,
                'finishloop': None,
                'finishpass': None,
                'interloops': {},
                'tallys': [],
                'onestartlist': True,
                'hidetimers': False,
                'startpasses': [],
                'finishpasses': [],
                'showuciids': False,
                'timelimit': None,
                'finished': False,
                'showinter': None,
                'intera': None,
                'interb': None,
                'interc': None,
                'interd': None,
                'intere': None,
            }
        })
        cr.add_section('irtt')
        cr.add_section('riders')
        cr.add_section('stagebonus')
        cr.add_section('stagepenalty')
        cr.merge(metarace.sysconf, 'irtt')
        if not cr.load(self.configfile):
            _log.info('%r not read, loading defaults', self.configfile)

        # load default gap
        self.startgap = tod.mktod(cr.get('irtt', 'startgap'))
        if self.startgap is None:
            self.startgap = tod.tod('1:00')

        # load result precision
        self.precision = cr.get_posint('irtt', 'precision', 1)
        if self.precision > 2:  # posint forbids negatives
            self.precision = 2

        # load start delay for wireless impulse
        self.startdelay = tod.mktod(cr.get('irtt', 'startdelay'))
        if self.startdelay is None:
            self.startdelay = tod.ZERO

        # load minimum elapsed time
        self.minelap = tod.mktod(cr.get('irtt', 'minelap'))
        if self.minelap is None:
            self.minelap = STARTFUDGE
        self.timelimit = cr.get('irtt', 'timelimit')  # save as str

        # allow auto export
        self.autoexport = cr.get_bool('irtt', 'autoexport')
        # sloppy start times
        self.sloppystart = cr.get_bool('irtt', 'sloppystart')
        # sloppy impulse mode (aka auto timing)
        self.sloppyimpulse = cr.get_bool('irtt', 'sloppyimpulse')
        # uci ids on startlists and results
        self.showuciids = cr.get_bool('irtt', 'showuciids')
        # count of finish passings to set finish time
        self.finishpass = cr.get_posint('irtt', 'finishpass', None)
        # source ID of start trigger decoder
        self.starttrig = cr.get('irtt', 'starttrig')  # by source
        # hide timer panes (for auto-timed setup)
        self.hidetimers = cr.get_bool('irtt', 'hidetimers')
        if self.hidetimers:
            self.timerframe.hide()

        # transponder timing options
        self.startloop = strops.chan2id(cr.get('irtt', 'startloop'))
        if self.startloop < 0:
            _log.warning('Invalid start loop channel ignored')
            self.startloop = None
        self.finishloop = strops.chan2id(cr.get('irtt', 'finishloop'))
        if self.finishloop < 0:
            _log.warning('Invalid finish loop channel ignored')
            self.finishloop = None
        if self.startloop is not None or self.finishloop is not None:
            if self.sloppyimpulse:
                configok = True
                if self.startloop is None or self.finishloop is None:
                    _log.error(
                        'Invalid timing mode: sloppyimpulse=%r, startloop=%r, finishloop=%r',
                        self.sloppyimpulse, self.startloop, self.finishloop)
                    self.sloppyimpulse = False
                else:
                    _log.debug(
                        'Auto impulse mode enabled: sloppyimpulse=%r, startloop=%r, finishloop=%r',
                        self.sloppyimpulse, self.startloop, self.finishloop)
                    if self.startloop == self.finishloop:
                        _log.debug(
                            'Shared start and finish loop, decoder impulses will not work'
                        )
            else:
                # timing is set by transponder passing time
                self.precision = 1
                _log.debug(
                    'Transponder timing mode, precision set to 1: startloop=%r finishloop=%r, sloppyimpulse=%r',
                    self.startloop, self.finishloop, self.sloppyimpulse)

        # load intermediate split schema
        self.showinter = strops.confopt_posint(cr.get('irtt', 'showinter'),
                                               None)
        self.ischem[COL_INTERA] = unjsob(cr.get('irtt', 'intera'))
        self.ischem[COL_INTERB] = unjsob(cr.get('irtt', 'interb'))
        self.ischem[COL_INTERC] = unjsob(cr.get('irtt', 'interc'))
        self.ischem[COL_INTERD] = unjsob(cr.get('irtt', 'interd'))
        self.ischem[COL_INTERE] = unjsob(cr.get('irtt', 'intere'))
        self.interloops = cr.get('irtt', 'interloops')

        # load _result_ categories
        self.loadcats(cr.get('irtt', 'categories'))

        # restore (stagerace) intermediates
        for i in cr.get('irtt', 'intermeds'):
            if i in RESERVED_SOURCES:
                _log.info('Ignoring reserved inter: %r', i)
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
                    places = strops.reformat_bibserplacelist(
                        cr.get(crkey, 'places'))
                if i not in self.intermeds:
                    _log.debug('Adding inter %r: %r %r', i, descr, places)
                    self.intermeds.append(i)
                    self.intermap[i] = {
                        'descr': descr,
                        'places': places,
                        'abbr': abbr,
                        'dist': km,
                        'show': doshow
                    }
                else:
                    _log.info('Ignoring duplicate inter: %r', i)

        # load contest meta data
        tallyset = set()
        for i in cr.get('irtt', 'contests'):
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
                    if pliststr and tally == '':  # no tally for these points!
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
                    allsrc = strops.confopt_bool(cr.get(crkey, 'all_source'))
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
        tallylist = cr.get('irtt', 'tallys')
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
                self.points[i] = {}  # redundant, but ok
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

        # re-join any existing timer state -> no, just do a start
        self.set_syncstart(tod.mktod(cr.get('irtt', 'start')),
                           tod.mktod(cr.get('irtt', 'lstart')))

        # re-load starters/results
        self.onestart = False
        for rs in cr.get('irtt', 'startlist').split():
            (r, s) = strops.bibstr2bibser(rs)
            self.addrider(r, s)
            wst = None
            tst = None
            ft = None
            pt = None
            ima = None
            imb = None
            imc = None
            imd = None
            ime = None
            lpass = None
            pcnt = 0
            nr = self.getrider(r, s)
            if cr.has_option('riders', rs):
                # bbb.sss = comment,wall_start,timy_start,finish,penalty,place
                ril = cr.get('riders', rs)  # vec
                lr = len(ril)
                if lr > 0:
                    nr[COL_COMMENT] = ril[0]
                if lr > 1:
                    wst = tod.mktod(ril[1])
                if lr > 2:
                    tst = tod.mktod(ril[2])
                if lr > 3:
                    ft = tod.mktod(ril[3])
                if lr > 4:
                    pt = tod.mktod(ril[4])
                if lr > 6:
                    ima = tod.mktod(ril[6])
                if lr > 7:
                    imb = tod.mktod(ril[7])
                if lr > 8:
                    imc = tod.mktod(ril[8])
                if lr > 9:
                    imd = tod.mktod(ril[9])
                if lr > 10:
                    ime = tod.mktod(ril[10])
                if lr > 11:
                    pcnt = strops.confopt_posint(ril[11])
                if lr > 12:
                    lpass = tod.mktod(ril[12])
            nri = self.getiter(r, s)
            self.settimes(nri, wst, tst, ft, pt, doplaces=False)
            self.setpasses(nri, pcnt)
            self.setinter(nri, ima, COL_INTERA)
            self.setinter(nri, imb, COL_INTERB)
            self.setinter(nri, imc, COL_INTERC)
            self.setinter(nri, imd, COL_INTERD)
            self.setinter(nri, ime, COL_INTERE)
            self.riders.set_value(nri, COL_LASTSEEN, lpass)
            # record any extra bonus/penalty to rider model
            if cr.has_option('stagebonus', rs):
                nr[COL_BONUS] = tod.mktod(cr.get('stagebonus', rs))
            if cr.has_option('stagepenalty', rs):
                nr[COL_PENALTY] = tod.mktod(cr.get('stagepenalty', rs))

        self.startpasses.clear()
        fp = cr.get('irtt', 'startpasses')
        if isinstance(fp, list):
            for p in fp:
                t = tod.mktod(p)
                if t is not None:
                    self.startpasses.insert(t, prec=4)

        self.finishpasses.clear()
        fp = cr.get('irtt', 'finishpasses')
        if isinstance(fp, list):
            for p in fp:
                t = tod.mktod(p)
                if t is not None:
                    self.finishpasses.insert(t, prec=4)

        # display config
        startmode = 'Strict'
        if self.sloppystart:
            startmode = 'Relaxed'
        timingmode = 'Armed'
        if self.sloppyimpulse:
            timingmode = 'Auto'
        elif self.finishloop is not None or self.startloop is not None:
            timingmode = 'Transponder'
        _log.info('Start mode: %s; Timing mode: %s; Precision: %d; Inters: %r',
                  startmode, timingmode, self.precision, self.interloops)

        # recalculate rankings
        self.placexfer()

        self.comment = cr.get('irtt', 'comment')
        self.arrivalcount = strops.confopt_posint(
            cr.get('irtt', 'arrivalcount'), 4)

        if strops.confopt_bool(cr.get('irtt', 'finished')):
            self.set_finished()
        self.onestartlist = strops.confopt_bool(cr.get('irtt', 'onestartlist'))

        # After load complete - check config and report. This ensures
        # an error message is left on top of status stack. This is not
        # always a hard fail and the user should be left to determine
        # an appropriate outcome.
        eid = cr.get('irtt', 'id')
        if eid and eid != EVENT_ID:
            _log.info('Event config mismatch: %r != %r', eid, EVENT_ID)
            self.readonly = True

    def saveconfig(self):
        """Save race to disk."""
        if self.readonly:
            _log.error('Attempt to save readonly event')
            return
        cw = jsonconfig.config()
        cw.add_section('irtt')
        if self.start is not None:
            cw.set('irtt', 'start', self.start.rawtime())
        if self.lstart is not None:
            cw.set('irtt', 'lstart', self.lstart.rawtime())
        cw.set('irtt', 'comment', self.comment)
        if self.startgap is not None:
            cw.set('irtt', 'startgap', self.startgap.rawtime(0))
        else:
            cw.set('irtt', 'startgap', None)
        if self.startdelay is not None:
            cw.set('irtt', 'startdelay', self.startdelay.rawtime())
        else:
            cw.set('irtt', 'startdelay', None)
        if self.minelap is not None:
            cw.set('irtt', 'minelap', self.minelap.rawtime())
        else:
            cw.set('irtt', 'minelap', None)

        fp = []
        for t in self.startpasses:
            fp.append(t[0].rawtime(5))
        cw.set('irtt', 'startpasses', fp)
        fp = []
        for t in self.finishpasses:
            fp.append(t[0].rawtime(5))
        cw.set('irtt', 'finishpasses', fp)

        cw.set('irtt', 'arrivalcount', self.arrivalcount)
        cw.set('irtt', 'sloppystart', self.sloppystart)
        cw.set('irtt', 'sloppyimpulse', self.sloppyimpulse)
        cw.set('irtt', 'autoexport', self.autoexport)
        cw.set('irtt', 'startloop', self.startloop)
        cw.set('irtt', 'starttrig', self.starttrig)
        cw.set('irtt', 'finishloop', self.finishloop)
        cw.set('irtt', 'finishpass', self.finishpass)
        cw.set('irtt', 'onestartlist', self.onestartlist)
        cw.set('irtt', 'showuciids', self.showuciids)
        cw.set('irtt', 'precision', self.precision)
        cw.set('irtt', 'timelimit', self.timelimit)
        cw.set('irtt', 'hidetimers', self.hidetimers)

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
        cw.set('irtt', 'intermeds', opinters)

        # save contest meta data
        cw.set('irtt', 'contests', self.contests)
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
        # save tally meta data
        cw.set('irtt', 'tallys', self.tallys)
        for i in self.tallys:
            crkey = 'tally_' + i
            cw.add_section(crkey)
            cw.set(crkey, 'descr', self.tallymap[i]['descr'])
            cw.set(crkey, 'keepdnf', self.tallymap[i]['keepdnf'])

        # save intermediate split schema
        cw.set('irtt', 'intera', jsob(self.ischem[COL_INTERA]))
        cw.set('irtt', 'interb', jsob(self.ischem[COL_INTERB]))
        cw.set('irtt', 'interc', jsob(self.ischem[COL_INTERC]))
        cw.set('irtt', 'interd', jsob(self.ischem[COL_INTERD]))
        cw.set('irtt', 'intere', jsob(self.ischem[COL_INTERE]))
        cw.set('irtt', 'interloops', self.interloops)
        cw.set('irtt', 'showinter', self.showinter)

        # save riders
        cw.add_section('stagebonus')
        cw.add_section('stagepenalty')
        cw.set('irtt', 'startlist', self.get_startlist())
        if self.autocats:
            cw.set('irtt', 'categories', ['AUTO'])
        else:
            cw.set('irtt', 'categories', self.get_catlist())
        cw.add_section('riders')
        for r in self.riders:
            if r[COL_BIB] != '':
                bib = r[COL_BIB]
                ser = r[COL_SERIES]
                bs = strops.bibser2bibstr(bib, ser)
                # place is saved for info only
                wst = ''
                if r[COL_WALLSTART] is not None:
                    wst = r[COL_WALLSTART].rawtime()
                tst = ''
                if r[COL_TODSTART] is not None:
                    tst = r[COL_TODSTART].rawtime()
                tft = ''
                if r[COL_TODFINISH] is not None:
                    tft = r[COL_TODFINISH].rawtime()
                tpt = ''
                if r[COL_TODPENALTY] is not None:
                    tpt = r[COL_TODPENALTY].rawtime()
                tima = ''
                if r[COL_INTERA] is not None:
                    tima = r[COL_INTERA].rawtime()
                timb = ''
                if r[COL_INTERB] is not None:
                    timb = r[COL_INTERB].rawtime()
                timc = ''
                if r[COL_INTERC] is not None:
                    timc = r[COL_INTERC].rawtime()
                timd = ''
                if r[COL_INTERD] is not None:
                    timd = r[COL_INTERD].rawtime()
                tine = ''
                if r[COL_INTERE] is not None:
                    tine = r[COL_INTERE].rawtime()
                pcnt = ''
                if r[COL_PASS] is not None:
                    pcnt = str(r[COL_PASS])
                lpass = ''
                if r[COL_LASTSEEN] is not None:
                    lpass = r[COL_LASTSEEN].rawtime()
                slice = [
                    r[COL_COMMENT], wst, tst, tft, tpt, r[COL_PLACE], tima,
                    timb, timc, timd, tine, pcnt, lpass
                ]
                cw.set('riders', bs, slice)
                if r[COL_BONUS] is not None:
                    cw.set('stagebonus', bs, r[COL_BONUS].rawtime())
                if r[COL_PENALTY] is not None:
                    cw.set('stagepenalty', bs, r[COL_PENALTY].rawtime())

        cw.set('irtt', 'finished', self.timerstat == 'finished')
        cw.set('irtt', 'id', EVENT_ID)
        _log.debug('Saving event config %r', self.configfile)
        with metarace.savefile(self.configfile) as f:
            cw.write(f)

    def get_ridercmdorder(self):
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
                descr = self.intermap[k]['descr']
            ret[k] = descr
        return ret

    def get_startlist(self):
        """Return a list of bibs in the rider model as b.s."""
        ret = []
        for r in self.riders:
            ret.append(strops.bibser2bibstr(r[COL_BIB], r[COL_SERIES]))
        return ' '.join(ret)

    def shutdown(self, win=None, msg='Exiting'):
        """Close event."""
        _log.debug('Event shutdown: %r', msg)
        if not self.readonly:
            self.saveconfig()
        self.winopen = False

    def reorder_signon(self):
        """Reorder riders for a sign on."""
        aux = []
        cnt = 0
        for r in self.riders:
            riderno = strops.riderno_key(r[COL_BIB])
            aux.append((riderno, cnt))
            cnt += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[1] for a in aux])
        return cnt

    def reorder_startlist(self, callup=False):
        """Reorder riders for a startlist."""
        aux = []
        cnt = 0
        for r in self.riders:
            st = tod.MAX
            if r[COL_WALLSTART] is not None:
                st = int(r[COL_WALLSTART].truncate(0).timeval)
            riderno = strops.riderno_key(r[COL_BIB])
            aux.append((st, riderno, cnt))
            cnt += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[2] for a in aux])
        return cnt

    def signon_report(self):
        """Return a signon report."""
        ret = []
        sec = report.signon_list('signon')
        self.reorder_signon()
        for r in self.riders:
            cmt = r[COL_COMMENT]
            sec.lines.append([cmt, r[COL_BIB], r[COL_NAMESTR]])
        ret.append(sec)
        return ret

    def callup_report(self):
        """Return startlist report."""
        return self.startlist_report()

    def startlist_report(self):
        """Return a startlist report."""
        self.reorder_startlist()
        ret = []
        if len(self.cats) > 1 and not self.onestartlist:
            for c in self.cats:
                #if c:
                ret.extend(self.startlist_report_gen(c))
                ret.append(report.pagebreak(0.05))
        else:
            ret = self.startlist_report_gen()
        return ret

    def startlist_report_gen(self, cat=None):
        catnamecache = {}
        catname = ''
        subhead = ''
        footer = ''
        uncat = False
        if cat is not None:
            dbr = self.meet.rdb.getrider(cat, 'cat')
            if dbr is not None:
                catname = self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST)
                subhead = self.meet.rdb.getvalue(dbr, riderdb.COL_LAST)
                footer = self.meet.rdb.getvalue(dbr, riderdb.COL_NOTE)
            if cat == '':
                catname = 'Uncategorised Riders'
                uncat = True
        else:
            cat = ''  # match all riders

        if self.onestartlist:
            for rc in self.get_catlist():
                dbr = self.meet.rdb.getrider(rc, 'cat')
                if dbr is not None:
                    cname = self.meet.rdb.getvalue(
                        dbr, riderdb.COL_FIRST)  # already decode
                    if cname:
                        catnamecache[rc] = cname
        """Return a startlist report (rough style)."""
        ret = []
        sec = report.rttstartlist('startlist')
        sec.heading = 'Startlist'
        if catname:
            sec.heading += ': ' + catname
            sec.subheading = subhead
        rcnt = 0
        cat = self.ridercat(cat)
        lt = None
        for r in self.riders:
            # add rider to startlist if primary cat matches
            bib = r[COL_BIB]
            series = r[COL_SERIES]
            cs = r[COL_CAT]
            pricat = riderdb.primary_cat(cs)
            rcat = self.ridercat(pricat)
            if self.onestartlist or cat == rcat:
                rcnt += 1
                ucicode = None
                name = r[COL_NAMESTR]
                if self.showuciids:
                    dbr = self.meet.rdb.getrider(bib, series)
                    if dbr is not None:
                        ucicode = self.meet.rdb.getvalue(
                            dbr, riderdb.COL_UCICODE)
                #if not ucicode and cat == u'':
                ## Rider may have a typo in cat, show the catlist
                #ucicode = cs
                comment = ''
                bstr = bib.upper()
                stxt = ''
                if r[COL_WALLSTART] is not None:
                    stxt = r[COL_WALLSTART].meridiem()
                    if lt is not None:
                        if r[COL_WALLSTART] - lt > self.startgap:
                            sec.lines.append([None, None, None])  # add space
                    lt = r[COL_WALLSTART]
                cstr = None
                if self.onestartlist and pricat != cat:
                    cstr = pricat
                    if cstr in catnamecache and len(catnamecache[cstr]) < 8:
                        cstr = catnamecache[cstr]
                sec.lines.append([stxt, bstr, name, ucicode, '____', cstr])
                if cstr in ['MB', 'WB']:
                    # lookup pilot - series lookup
                    dbr = self.meet.rdb.getrider(r[COL_BIB], 'pilot')
                    if dbr is not None:
                        puci = self.meet.rdb.getvalue(dbr, riderdb.COL_UCICODE)
                        pnam = strops.listname(
                            self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                            self.meet.rdb.getvalue(dbr, riderdb.COL_LAST),
                            self.meet.rdb.getvalue(dbr, riderdb.COL_ORG))
                        sec.lines.append(['', '', pnam, puci, '', 'pilot'])

        ret.append(sec)
        if rcnt > 1:
            sec = report.bullet_text('ridercnt')
            sec.lines.append(['', 'Total riders: ' + str(rcnt)])
            ret.append(sec)
        return ret

    def arrival_report(self, limit=0):
        """Return an arrival report."""
        # build aux table
        aux = []
        nowtime = tod.now()
        count = 0
        for r in self.riders:
            reta = tod.MAX
            rarr = tod.MAX
            plstr = r[COL_PLACE]
            bstr = r[COL_BIB]
            nstr = r[COL_NAMESTR]
            turnstr = ''
            ets = ''
            speedstr = ''
            rankstr = ''
            noshow = False
            cs = r[COL_CAT]
            cat = self.ridercat(riderdb.primary_cat(cs))
            i = self.getiter(r[COL_BIB], r[COL_SERIES])
            if plstr.isdigit():  # rider placed at finish
                ## only show for a short while
                until = r[COL_TODFINISH] + ARRIVALTIMEOUT
                if nowtime < until:
                    rarr = r[COL_TODFINISH]
                    et = self.getelapsed(i)
                    reta = et
                    ets = et.rawtime(self.precision)
                    rankstr = '(' + plstr + '.)'
                    speedstr = ''
                    # cat distance should override this
                    if self.meet.distance is not None:
                        speedstr = et.speedstr(1000.0 * self.meet.distance)
                else:
                    noshow = True
                    speedstr = ''
            elif r[COL_ETA] is not None:
                # append km mark if available
                if r[COL_PASS] > 0:
                    nstr += (' @ km' + str(r[COL_PASS]))
                # projected finish time
                ets = '*' + r[COL_ETA].rawtime(self.precision)
                reta = r[COL_ETA]

            if self.showinter is not None and self.showinter in self.ischem and self.ischem[
                    self.showinter] is not None:
                # show time at the turnaround
                trk = self.inters[self.showinter][cat].rank(
                    r[COL_BIB], r[COL_SERIES])
                if trk is not None:
                    tet = self.inters[self.showinter][cat][trk][0]
                    tplstr = str(trk + 1)
                    trankstr = ' (' + tplstr + '.)'
                    turnstr = tet.rawtime(self.precision) + trankstr
                    if not speedstr:
                        # override speed from turn
                        speedstr = ''
                        dist = self.ischem[self.showinter]['dist']
                        if dist is not None:
                            speedstr = tet.speedstr(1000.0 * dist)
                else:
                    pass

            if not noshow:
                if ets or speedstr:  # only add riders with an estimate
                    aux.append((rarr, reta, count,
                                [rankstr, bstr, nstr, turnstr, ets, speedstr]))
                    count += 1

        # reorder by arrival times
        aux.sort()

        # transfer rows into report section and return
        sec = report.section('arrivals')
        intlbl = None
        if self.showinter is not None:
            intlbl = 'Inter'
        if len(self.interloops) > 0:
            sec.heading = 'Riders On Course'
            sec.footer = '* denotes projected finish time.'
        else:
            sec.heading = 'Recent Arrivals'
        sec.colheader = [None, None, None, intlbl, 'Finish', 'Avg']
        for r in aux:
            hr = r[3]
            sec.lines.append(hr)
        ret = []
        if len(sec.lines) > 0:
            ret.append(sec)
        return ret

    def analysis_report(self):
        """Return judges report."""
        return self.camera_report()

    def camera_report(self):
        """Return a judges report."""

        # build aux table
        aux = []
        count = 0
        for r in self.riders:
            if r[COL_COMMENT] or r[COL_TODFINISH] is not None:
                # include on camera report
                bstr = strops.bibser2bibstr(r[COL_BIB], r[COL_SERIES])
                riderno = strops.riderno_key(bstr)
                rorder = strops.dnfcode_key(r[COL_COMMENT])
                nstr = r[COL_NAMESTR]
                plstr = r[COL_PLACE]
                rkstr = ''
                if plstr and plstr.isdigit():
                    rk = int(plstr)
                    if rk < 6:  # annotate top 5 places
                        rkstr = ' (' + plstr + '.)'
                sts = '-'
                if r[COL_TODSTART] is not None:
                    sts = r[COL_TODSTART].rawtime(2)
                elif r[COL_WALLSTART] is not None:
                    sts = r[COL_WALLSTART].rawtime(0) + '   '
                fts = '-'
                ft = tod.MAX
                if r[COL_TODFINISH] is not None:
                    ft = r[COL_TODFINISH]
                    fts = r[COL_TODFINISH].rawtime(2)

                i = self.getiter(r[COL_BIB], r[COL_SERIES])
                et = self.getelapsed(i)
                ets = '-'
                unplaced = False
                if et is not None:
                    ets = et.rawtime(self.precision)
                elif r[COL_COMMENT] != '':
                    rkstr = r[COL_COMMENT]
                    unplaced = True
                aux.append((rorder, ft, riderno, count, unplaced,
                            [rkstr, bstr, nstr, sts, fts, ets]))

        # reorder by arrival at finish
        aux.sort()

        # transfer to report section
        count = 0
        sec = report.section('analysis')
        sec.heading = 'Judges Report'
        sec.colheader = ['Hit', None, None, 'Start', 'Fin', 'Net']
        for r in aux:
            hr = r[5]
            if not r[4]:
                hr[0] = str(count + 1) + hr[0]
            sec.lines.append(hr)
            count += 1
            if count % 10 == 0:
                sec.lines.append([None, None, None])
        ret = []
        if len(sec.lines) > 0:
            ret.append(sec)
        return ret

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
                n, s = strops.bibstr2bibser(bib)
                r = self.getrider(n, s)
                tallytot += self.points[tally][bib]
                aux.append((self.points[tally][bib], self.pointscb[tally][bib],
                            -strops.riderno_key(bib), -cnt, [
                                None, r[COL_BIB], r[COL_NAMESTR],
                                strops.truncpad(str(self.pointscb[tally][bib]),
                                                10,
                                                ellipsis=False), None,
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
        if len(aux) > 0:
            aux.sort(reverse=True)
            sec = report.section('bonus')
            sec.heading = 'Time Bonuses'
            sec.units = 'sec'
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

        if len(ret) == 0:
            _log.warning('No data available for points report')
        return ret

    def catresult_report(self):
        """Return a categorised result report."""
        ret = []
        for cat in self.cats:
            ret.extend(self.single_catresult(cat))
        return ret

    def single_catresult(self, cat=''):
        ret = []
        allin = False
        catname = cat
        if cat == '':
            if len(self.cats) > 1:
                catname = 'Uncategorised Riders'
            else:
                # There is only one cat - so all riders are in it
                allin = True
        subhead = ''
        footer = ''
        distance = self.meet.distance  # fall on meet dist
        dbr = self.meet.rdb.getrider(cat, 'cat')
        if dbr is not None:
            catname = self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST)
            subhead = self.meet.rdb.getvalue(dbr, riderdb.COL_LAST)
            footer = self.meet.rdb.getvalue(dbr, riderdb.COL_NOTE)
            dist = self.meet.rdb.getvalue(dbr, riderdb.COL_REFID)
            if dist:
                try:
                    distance = float(dist)
                except Exception:
                    _log.warning('Invalid distance %r for cat %r', dist, cat)
        sec = report.section('result-' + cat)
        ct = None
        lt = None
        lpstr = None
        totcount = 0
        dnscount = 0
        dnfcount = 0
        hdcount = 0
        fincount = 0
        for r in self.riders:  # scan whole list even though cat are sorted.
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
                    incat = rcats[0] not in self.cats  # backward logic
            if incat:
                if cat:
                    rcat = cat
                else:
                    rcat = rcats[0]  # (work-around mis-categorised rider)
                placed = False
                totcount += 1
                i = self.getiter(r[COL_BIB], r[COL_SERIES])
                ft = self.getelapsed(i)
                bstr = r[COL_BIB]
                nstr = r[COL_NAMESTR]
                cstr = ''
                if cat == '':  # categorised result does not need cat
                    cstr = rcat
                if self.showuciids:
                    dbr = self.meet.rdb.getrider(bstr, self.series)
                    if dbr is not None:
                        cstr = self.meet.rdb.getvalue(dbr, riderdb.COL_UCICODE)
                if ct is None:
                    ct = ft
                pstr = None
                if r[COL_PLACE] != '' and r[COL_PLACE].isdigit():
                    pstr = r[COL_PLACE] + '.'
                    fincount += 1  # only count placed finishers
                    placed = True
                else:
                    pstr = r[COL_COMMENT]
                    # 'special' dnfs
                    if pstr == 'dns':
                        dnscount += 1
                    elif pstr == 'otl':
                        hdcount += 1
                    else:
                        if pstr:  # commented dnf
                            dnfcount += 1
                    if pstr:
                        placed = True
                        if lpstr != pstr:
                            ## append an empty row
                            sec.lines.append(
                                [None, None, None, None, None, None])
                            lpstr = pstr
                tstr = None
                if ft is not None:
                    tstr = ft.rawtime(self.precision)
                dstr = None
                if ct is not None and ft is not None and ct != ft:
                    dstr = '+' + (ft - ct).rawtime(1)
                if placed:
                    sec.lines.append([pstr, bstr, nstr, cstr, tstr, dstr])
                    if cat in ['WB', 'MB']:  #also look up pilots
                        # lookup pilot - series lookup
                        dbr = self.meet.rdb.getrider(r[COL_BIB], 'pilot')
                        if dbr is not None:
                            puci = self.meet.rdb.getvalue(
                                dbr, riderdb.COL_UCICODE)
                            pnam = strops.listname(
                                self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                                self.meet.rdb.getvalue(dbr, riderdb.COL_LAST),
                                self.meet.rdb.getvalue(dbr, riderdb.COL_ORG))
                            sec.lines.append(['', 'pilot', pnam, puci, '', ''])

        residual = totcount - (fincount + dnfcount + dnscount + hdcount)

        if self.timerstat == 'finished':  # THIS OVERRIDES RESIDUAL
            sec.heading = 'Result'
        else:
            if self.racestat == 'prerace':
                sec.heading = ''  # anything better?
            else:
                if residual > 0:
                    sec.heading = 'Standings'
                else:
                    sec.heading = 'Provisional Result'

        # Append all result categories and uncat if riders
        if cat or totcount > 0:
            ret.append(sec)
            rsec = sec
            # Race metadata / UCI comments
            sec = report.bullet_text('uci' + cat)
            if ct is not None:
                if distance is not None:
                    avgprompt = 'Average speed of the winner: '
                    if residual > 0:
                        avgprompt = 'Average speed of the leader: '
                    sec.lines.append(
                        [None, avgprompt + ct.speedstr(1000.0 * distance)])
            sec.lines.append(
                [None, 'Number of starters: ' + str(totcount - dnscount)])
            if hdcount > 0:
                sec.lines.append([
                    None,
                    'Riders finishing out of time limits: ' + str(hdcount)
                ])
            if dnfcount > 0:
                sec.lines.append(
                    [None, 'Riders abandoning the race: ' + str(dnfcount)])
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
        """Return a race result report."""
        ret = []
        # dump results
        self.placexfer()  # ensure all cat places are filled
        # also re-announces!
        if self.timerstat == 'running':
            # until final, show last few
            ret.extend(self.arrival_report(self.arrivalcount))
        if len(self.cats) > 1:
            ret.extend(self.catresult_report())
        else:
            ret.extend(self.single_catresult())

        # Decisions of commissaires panel
        if len(self.comment) > 0:
            sec = report.bullet_text('comms')
            sec.heading = 'Decisions of the Commissaires Panel'
            for cl in self.comment:
                sec.lines.append([None, cl.strip()])
            ret.append(sec)
        return ret

    def startlist_gen(self, cat=''):
        """Generator function to export a startlist."""
        mcat = self.ridercat(cat)
        self.reorder_startlist()
        for r in self.riders:
            cs = r[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if mcat == '' or mcat == rcat:
                start = ''
                if r[COL_WALLSTART] is not None:
                    start = r[COL_WALLSTART].rawtime(0)
                bib = r[COL_BIB]
                series = r[COL_SERIES]
                name = ''
                ## replace with rmap lookup
                dbr = self.meet.rdb.getrider(bib, series)
                if dbr is not None:
                    name = strops.listname(
                        self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                        self.meet.rdb.getvalue(dbr, riderdb.COL_LAST), None,
                        16)
                ## to here
                cat = cs
                yield [start, bib, series, name, cat]

    def lifexport(self):
        return []

    def get_elapsed(self):
        return None

    def result_gen(self, cat=''):
        """Generator function to export a final result."""
        self.placexfer()
        mcat = self.ridercat(cat)
        rcount = 0
        lrank = None
        lpl = None
        for r in self.riders:
            cs = r[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if mcat == '' or mcat == rcat:
                bib = r[COL_BIB]
                ser = r[COL_SERIES]
                bs = strops.bibser2bibstr(bib, ser)
                i = self.getiter(bib, ser)
                ft = self.getelapsed(i)
                if ft is not None:
                    ft = ft.truncate(self.precision)
                crank = None
                rank = None
                if r[COL_PLACE].isdigit():
                    rcount += 1
                    rank = int(r[COL_PLACE])
                    if rank != lrank:
                        crank = rcount
                    else:
                        crank = lpl
                    lpl = crank
                    lrank = rank
                else:
                    crank = r[COL_COMMENT]
                extra = None
                if r[COL_WALLSTART] is not None:
                    extra = r[COL_WALLSTART]

                # stage bonuses and penalties
                bonus = None
                if bs in self.bonuses or r[COL_BONUS] is not None:
                    bonus = tod.mkagg(0)
                    if bs in self.bonuses:
                        bonus += self.bonuses[bs]
                    if r[COL_BONUS] is not None:
                        bonus += r[COL_BONUS]

                penalty = None
                if r[COL_PENALTY] is not None:
                    penalty = r[COL_PENALTY]

                yield [crank, bs, ft, bonus, penalty]

    def main_loop(self, cb):
        """Run callback once in main loop idle handler."""
        cb('')
        return False

    def set_syncstart(self, start=None, lstart=None):
        if start is not None:
            if lstart is None:
                lstart = start
            self.start = start
            self.lstart = lstart
            self.timerstat = 'running'
            self.meet.stat_but.buttonchg(uiutil.bg_armstart, 'Running')
            self.meet.stat_but.set_sensitive(True)
            _log.info('Timer sync @ %s', start.rawtime(2))
            self.sl.toidle()
            self.fl.toidle()

    def rfidinttrig(self, lr, e, bibstr, bib, series):
        """Register Intermediate RFID crossing."""
        st = lr[COL_WALLSTART]
        if lr[COL_TODSTART] is not None:
            st = lr[COL_TODSTART]
        chan = strops.chan2id(e.chan)
        if chan not in self.interloops:
            _log.info(
                'Intermediate passing from unconfigured loop: %s:%s@%s/%s',
                e.refid, e.chan, e.rawtime(2), e.source)
        if st is not None and e > st and e - st > STARTFUDGE:
            if lr[COL_TODFINISH] is None:
                # Got a rider on course, find out where they _should_ be
                self.doannounce = True
                elap = e - st
                # find first matching split point
                split = None
                for isplit in self.interloops[chan]:
                    minelap = self.ischem[isplit]['minelap']
                    maxelap = self.ischem[isplit]['maxelap']
                    if lr[isplit] is None:
                        if elap > minelap and elap < maxelap:
                            split = isplit
                            break

                if split is not None:
                    # save and announce arrival at intermediate
                    nri = self.getiter(bib, series)
                    rank = self.setinter(nri, e, split)
                    place = '(' + str(rank + 1) + '.)'
                    namestr = lr[COL_NAMESTR]
                    cs = lr[COL_CAT]
                    rcat = self.ridercat(riderdb.primary_cat(cs))
                    # use cat field for split label
                    label = self.ischem[split]['label']
                    rts = ''
                    rt = self.inters[split][rcat][rank][0]
                    if rt is not None:
                        rts = rt.rawtime(2)
                    ##self.meet.scb.add_rider([place,bib,namestr,label,rts],
                    ##'ttsplit')
                    _log.info('Intermediate %s: %s %s:%s@%s/%s', label, place,
                              bibstr, e.chan, e.rawtime(2), e.source)
                    lr[COL_ETA] = self.geteta(nri)
                else:
                    _log.info('No match for intermediate: %s:%s@%s/%s', bibstr,
                              e.chan, e.rawtime(2), e.source)
            else:
                _log.info('Intermediate finished rider: %s:%s@%s/%s', bibstr,
                          e.chan, e.rawtime(2), e.source)
        else:
            _log.info('Intermediate rider not yet on course: %s:%s@%s/%s',
                      bibstr, e.chan, e.rawtime(2), e.source)
        return False

    def start_by_rfid(self, lr, e, bibstr):
        # ignore already finished rider
        if lr[COL_TODFINISH] is not None:
            _log.info('Finished rider on startloop: %s:%s@%s/%s', bibstr,
                      e.chan, e.rawtime(2), e.source)
            return False

        # ignore passings if start not properly armed
        if not self.sloppystart:
            if lr[COL_TODSTART] is not None:
                _log.info('Started rider on startloop: %s:%s@%s/%s', bibstr,
                          e.chan, e.rawtime(2), e.source)
                return False
            # compare wall and actual starts
            if lr[COL_WALLSTART] is not None:
                wv = lr[COL_WALLSTART].timeval
                ev = e.timeval
                if abs(wv - ev) > 5:  # differ by more than 5 secs
                    _log.info('Ignored start time: %s:%s@%s/%s != %s', bibstr,
                              e.chan, e.rawtime(2), e.source,
                              lr[COL_WALLSTART].rawtime(0))
                    return False

        i = self.getiter(lr[COL_BIB], lr[COL_SERIES])
        if self.sloppyimpulse:
            self.start_match(i, e, bibstr)
        else:
            _log.info('Set start time: %s:%s@%s/%s', bibstr, e.chan,
                      e.rawtime(2), e.source)
            self.settimes(i, tst=e)
        return False

    def finish_match(self, i, st, e, bibstr):
        """Find impulse matching this passing"""
        # finish transponder loop should be positioned around finish switch
        match = None
        count = 0
        for p in reversed(self.finishpasses):
            oft = abs(e.timeval - p[0].timeval)
            if e > p[0] and oft > _FINISH_MATCH_THRESH.timeval:
                break
            elif oft < _FINISH_MATCH_THRESH:
                match = p[0]
                count += 1

        # if rider wheels are overlapped, print a warning
        if count > 2:
            _log.warning(
                'Excess impulses detected for %s @ %s, manual check required',
                bibstr, e.rawtime(2))

        if match is not None:
            _log.info(
                'Set finish time: %s from passing %s:%s@%s/%s, %d matches',
                match.rawtime(4), bibstr, e.chan, e.rawtime(2), e.source,
                count)
            self.settimes(i, tst=st, tft=match)
        else:
            _log.warning('No finish match found for passing %s:%s@%s/%s',
                         bibstr, e.chan, e.rawtime(2), e.source)

    def start_match(self, i, e, bibstr):
        """Find impulse matching this passing"""
        # start transponder loop must be positioned after start switch
        match = None
        for p in reversed(self.startpasses):
            if e > p[0]:
                if e - p[0] < _START_MATCH_THRESH:
                    match = p[0]
                else:
                    break

        if match is not None:
            _log.info('Set start time: %s from passing %s:%s@%s/%s',
                      match.rawtime(4), bibstr, e.chan, e.rawtime(2), e.source)
            self.settimes(i, tst=match)
        else:
            _log.warning('No start match found for passing %s:%s@%s/%s',
                         bibstr, e.chan, e.rawtime(2), e.source)

    def setrftime(self, bib, rank, rftime, bonus=None):
        """Override rider result from CSV Data."""
        _log.info('Set finish time from CSV: ' + bib + '@' + rftime.rawtime(2))
        i = self.getiter(bib, '')
        self.settimes(i, tft=rftime)

    def setriderval(self, bib, rank, bunch, bonus=None):
        """Hook for CSV import - assume bunch holds elapsed only."""
        _log.debug('Set rider val by elapsed time: ' + bib + '/' +
                   bunch.rawtime(2))
        i = self.getiter(bib, '')
        self.settimes(i, tst=tod.ZERO, tft=bunch)

    def finish_by_rfid(self, lr, e, bibstr):
        if lr[COL_TODFINISH] is not None:
            _log.info('Finished rider seen on finishloop: %s:%s@%s/%s', bibstr,
                      e.chan, e.rawtime(2), e.source)
            return False

        if lr[COL_WALLSTART] is None and lr[COL_TODSTART] is None:
            _log.warning('No start time for rider at finish: %s:%s@%s/%s',
                         bibstr, e.chan, e.rawtime(2), e.source)
            return False

        cs = lr[COL_CAT]
        cat = self.ridercat(riderdb.primary_cat(cs))
        finishpass = self.finishpass
        if cat in self.catlaps:
            finishpass = self.catlaps[cat]
            _log.debug('%r laps=%s, cat=%r', bibstr, finishpass, cat)

        if finishpass is None:
            st = lr[COL_WALLSTART]
            if lr[COL_TODSTART] is not None:
                st = lr[COL_TODSTART]  # use tod if avail
            if e > st + self.minelap:
                i = self.getiter(lr[COL_BIB], lr[COL_SERIES])
                if self.sloppyimpulse:
                    self.finish_match(i, lr[COL_TODSTART], e, bibstr)
                else:
                    self.settimes(i, tst=lr[COL_TODSTART], tft=e)
                    _log.info('Set finish time: %s:%s@%s/%s', bibstr, e.chan,
                              e.rawtime(2), e.source)
            else:
                _log.info('Ignored early finish: %s:%s@%s/%s', bibstr, e.chan,
                          e.rawtime(2), e.source)
        else:
            lt = lr[COL_WALLSTART]
            if lr[COL_TODSTART] is not None:
                lt = lr[COL_TODSTART]
            if lr[COL_LASTSEEN] is not None and lr[COL_LASTSEEN] > lt:
                lt = lr[COL_LASTSEEN]
            if e > lt + self.minelap:
                lr[COL_PASS] += 1
                nc = lr[COL_PASS]
                if nc >= finishpass:
                    i = self.getiter(lr[COL_BIB], lr[COL_SERIES])
                    if self.sloppyimpulse:
                        self.finish_match(i, lr[COL_TODSTART], e, bibstr)
                    else:
                        self.settimes(i, tst=lr[COL_TODSTART], tft=e)
                        _log.info('Set finish lap time: %s:%s@%s/%s', bibstr,
                                  e.chan, e.rawtime(2), e.source)
                else:
                    _log.info('Lap %s passing: %s:%s@%s/%s', nc, bibstr,
                              e.chan, e.rawtime(2), e.source)
            else:
                _log.info('Ignored short lap: %s:%s@%s/%s', bibstr, e.chan,
                          e.rawtime(2), e.source)

        # save a copy of this passing
        lr[COL_LASTSEEN] = e

        return False

    def timertrig(self, e):
        """Process transponder passing event."""
        chan = strops.chan2id(e.chan)
        if e.refid in ['', '255']:
            if self.finishloop is not None and chan == self.finishloop:
                self.fin_trig(e)
            elif self.startloop is not None and chan == self.startloop:
                self.start_trig(e)
            else:
                _log.info('Spurious trigger: %s@%s/%s', e.chan, e.rawtime(2),
                          e.source)
            return False

        r = self.meet.rdb.getrefid(e.refid)
        if r is None:
            _log.info('Unknown rider: %s:%s@%s/%s', e.refid, e.chan,
                      e.rawtime(2), e.source)
            return False

        bib = self.meet.rdb.getvalue(r, riderdb.COL_BIB)
        series = self.meet.rdb.getvalue(r, riderdb.COL_SERIES)
        lr = self.getrider(bib, series)
        if lr is not None:
            # distinguish a shared finish / start loop
            okfin = False
            st = lr[COL_WALLSTART]
            if lr[COL_TODSTART] is not None:
                st = lr[COL_TODSTART]
            if st is not None and e > st and e - st > self.minelap:
                okfin = True

            bibstr = strops.bibser2bibstr(bib, series)

            # switch on loop source mode
            if okfin and self.finishloop is not None and chan == self.finishloop:
                return self.finish_by_rfid(lr, e, bibstr)
            elif self.startloop is not None and chan == self.startloop:
                return self.start_by_rfid(lr, e, bibstr)
            elif chan in self.interloops:
                return self.rfidinttrig(lr, e, bibstr, bib, series)
            elif self.finishloop is not None and chan == self.finishloop:
                # handle the case where source matches, but timing is off
                _log.info('Early arrival at finish: %s:%s@%s/%s', bibstr,
                          e.chan, e.rawtime(2), e.source)
                return False

            if lr[COL_TODFINISH] is not None:
                _log.info('Finished rider: %s:%s@%s/%s', bibstr, e.chan,
                          e.rawtime(2), e.source)
                return False

            if self.fl.getstatus() not in ['armfin']:
                st = lr[COL_WALLSTART]
                if lr[COL_TODSTART] is not None:
                    st = lr[COL_TODSTART]
                if st is not None and e > st and e - st > self.minelap:
                    self.fl.setrider(lr[COL_BIB], lr[COL_SERIES])
                    self.armfinish()
                    _log.info('Arm finish: %s:%s@%s/%s', bibstr, e.chan,
                              e.rawtime(2), e.source)
                else:
                    _log.info('Early arrival at finish: %s:%s@%s/%s', bibstr,
                              e.chan, e.rawtime(2), e.source)
            else:
                _log.info('Finish blocked: %s:%s@%s/%s', bibstr, e.chan,
                          e.rawtime(2), e.source)
        else:
            _log.info('Non-starter: %s:%s@%s/%s', bibstr, e.chan, e.rawtime(2),
                      e.source)
        return False

    def int_trig(self, t):
        """Register intermediate trigger."""
        _log.info('Intermediate cell: %s', t.rawtime(2))

    def fin_trig(self, t):
        """Register finish trigger."""
        _log.info('Finish trigger %s@%s/%s', t.chan, t.rawtime(4), t.source)
        if self.timerstat == 'running':
            if self.fl.getstatus() == 'armfin':
                bib = self.fl.bibent.get_text()
                series = self.fl.serent.get_text()
                i = self.getiter(bib, series)
                if i is not None:
                    cs = self.riders.get_value(i, COL_CAT)
                    cat = self.ridercat(riderdb.primary_cat(cs))
                    self.curcat = cat
                    self.settimes(i,
                                  tst=self.riders.get_value(i, COL_TODSTART),
                                  tft=t)
                    self.fl.tofinish()
                    ft = self.getelapsed(i)
                    if ft is not None:
                        self.fl.set_time(ft.timestr(2))
                        rank = self.results[cat].rank(bib, series) + 1
                        self.announce_rider(
                            str(rank),
                            bib,
                            self.riders.get_value(i, COL_NAMESTR),
                            self.riders.get_value(i, COL_SHORTNAME),
                            cat,
                            et=ft)  # announce the raw elapsed time
                        # send a flush hint to minimise display lag
                        self.meet.cmd_announce('redraw', 'timer')
                    else:
                        self.fl.set_time('[err]')

                else:
                    _log.error('Missing rider at finish')
                    self.sl.toidle()
            # save passing to start passing store
            self.finishpasses.insert(t, prec=4)
        elif self.timerstat == 'armstart':
            self.set_syncstart(t)

    def start_trig(self, t):
        """Register start trigger."""
        _log.info('Start trigger %s@%s/%s', t.chan, t.rawtime(4), t.source)
        if self.timerstat == 'running':
            # apply start trig to start line rider
            nst = t - self.startdelay
            if self.sl.getstatus() == 'armstart':
                i = self.getiter(self.sl.bibent.get_text(),
                                 self.sl.serent.get_text())
                if i is not None:
                    self.settimes(i, tst=nst, doplaces=False)
                    self.sl.torunning()
                else:
                    _log.error('Missing rider at start')
                    self.sl.toidle()
            # save passing to start passing store
            self.startpasses.insert(t, prec=4)
        elif self.timerstat == 'armstart':
            self.set_syncstart(t, tod.now())

    def alttimertrig(self, e):
        """Handle chronometer callbacks."""
        # note: these impulses are sourced from alttimer device and keyboard
        #       transponder triggers are collected separately in timertrig()
        channo = strops.chan2id(e.chan)
        if channo == 0:
            self.start_trig(e)
        elif channo == 1:
            self.fin_trig(e)
        else:
            _log.info('%s@%s/%s', e.chan, e.rawtime(), e.source)
        return False

    def on_start(self, curoft):
        # use search instead of lookup to avoid the tosw problem
        for r in self.riders:
            ws = r[COL_WALLSTART]
            if ws is not None:
                if curoft + tod.tod('30') == ws:
                    bib = r[COL_BIB]
                    ser = r[COL_SERIES]
                    _log.info('pre-load starter: ' + repr(bib))
                    self.sl.setrider(bib, ser)
                    self.meet.cmd_announce('startline', bib)
                    break
                if curoft + tod.tod('5') == ws:
                    bib = r[COL_BIB]
                    ser = r[COL_SERIES]
                    _log.info('Load starter: ' + repr(bib))
                    self.sl.setrider(bib, ser)
                    self.sl.toarmstart()
                    self.start_unload = ws + tod.tod('5')
                    break

    def timeout(self):
        """Update slow changing aspects of race."""
        if not self.winopen:
            return False
        if self.timerstat == 'running':
            nowoft = (tod.now() - self.lstart).truncate(0)

            # auto load/clear start lane if not in sloppy impulse mode
            if not self.sloppyimpulse:
                if self.sl.getstatus() in ['idle', 'load']:
                    if nowoft.timeval % 5 == 0:  # every five
                        self.on_start(nowoft)
                else:
                    if nowoft == self.start_unload:
                        self.sl.toidle()

            # after manips, then re-set start time
            self.sl.set_time(nowoft.timestr(0))

            # if finish lane loaded, set the elapsed time
            if self.fl.getstatus() in ['load', 'running', 'armfin']:
                bib = self.fl.bibent.get_text()
                series = self.fl.serent.get_text()
                i = self.getiter(bib, series)
                if i is not None:
                    et = self.getelapsed(i, runtime=True)
                    self.fl.set_time(et.timestr(0))
                    self.announce_rider('',
                                        bib,
                                        self.riders.get_value(i, COL_NAMESTR),
                                        self.riders.get_value(
                                            i, COL_SHORTNAME),
                                        self.riders.get_value(i, COL_CAT),
                                        rt=et)  # announce running time

        if self.doannounce:
            self.doannounce = False
            GLib.idle_add(self.delayed_announce)
            if self.autoexport:
                GLib.idle_add(self.doautoexport)
        return True

    def doautoexport(self, data=None):
        """Run an export process."""
        self.meet.menu_data_results_cb(None)
        return False

    def clearplaces(self):
        """Clear rider place makers and re-order out riders"""
        self.bonuses = {}
        for c in self.tallys:  # points are grouped by tally
            self.points[c] = {}
            self.pointscb[c] = {}
        aux = []
        count = 0
        for r in self.riders:
            r[COL_PLACE] = r[COL_COMMENT]
            riderno = strops.riderno_key(r[COL_BIB])
            rplace = strops.dnfcode_key(r[COL_COMMENT])
            aux.append((rplace, riderno, count))
            count += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[2] for a in aux])

    def getrider(self, bib, series=''):
        """Return temporary reference to model row."""
        ret = None
        for r in self.riders:
            if r[COL_BIB] == bib and r[COL_SERIES] == series:
                ret = r
                break
        return ret

    def starttime(self, start=None, bib='', series=''):
        """Adjust start time for the rider."""
        r = self.getrider(bib, series)
        if r is not None:
            r[COL_WALLSTART] = start
            #self.unstart(bib, series, wst=start)

    def delrider(self, bib='', series=''):
        """Delete the specificed rider from the race model."""
        i = self.getiter(bib, series)
        if i is not None:
            self.riders.remove(i)

    def addrider(self, bib='', series=''):
        """Add specified rider to race model."""
        if bib == '' or self.getrider(bib, series) is None:
            ## could be a rmap lookup here
            nr = [
                bib, series, '', '', '', None, None, None, tod.ZERO, '', '',
                None, None, None, None, None, None, None, 0, None, None
            ]
            dbr = self.meet.rdb.getrider(bib, series)
            if dbr is not None:
                nr[COL_NAMESTR] = strops.listname(
                    self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                    self.meet.rdb.getvalue(dbr, riderdb.COL_LAST),
                    self.meet.rdb.getvalue(dbr, riderdb.COL_ORG))
                nr[COL_CAT] = self.meet.rdb.getvalue(dbr, riderdb.COL_CAT)
                nr[COL_SHORTNAME] = strops.fitname(
                    self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                    self.meet.rdb.getvalue(dbr, riderdb.COL_LAST), 12)
            return self.riders.append(nr)
        else:
            return None

    def info_time_edit_clicked_cb(self, button, data=None):
        """Toggle the visibility of timer panes"""
        self.hidetimers = not self.hidetimers
        if self.hidetimers:
            self.timerframe.hide()
        else:
            self.timerframe.show()

    def editcol_cb(self, cell, path, new_text, col):
        """Update value in edited cell."""
        new_text = new_text.strip()
        if col == COL_BIB:
            if new_text.isalnum():
                if self.getrider(new_text,
                                 self.riders[path][COL_SERIES]) is None:
                    self.riders[path][COL_BIB] = new_text
                    dbr = self.meet.rdb.getrider(new_text, self.series)
                    if dbr is not None:
                        nr[COL_NAMESTR] = strops.listname(
                            self.meet.rdb.getvalue(dbr, riderdb.COL_FIRST),
                            self.meet.rdb.getvalue(dbr, riderdb.COL_LAST),
                            self.meet.rdb.getvalue(dbr, riderdb.COL_ORG))
                        nr[COL_CAT] = self.meet.rdb.getvalue(
                            dbr, riderdb.COL_CAT)
        elif col == COL_PASS:
            if new_text.isdigit():
                self.riders[path][COL_PASS] = int(new_text)
                _log.debug('Adjusted pass count: %r:%r',
                           self.riders[path][COL_BIB],
                           self.riders[path][COL_PASS])
        else:
            self.riders[path][col] = new_text.strip()

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
                else:
                    ret = limit
            if ret is None:
                _log.warning('Unable to decode time limit: %r', limitstr)
        return ret

    def placexfer(self):
        """Transfer places into model."""
        self.places = ''
        placelist = []
        #note: clearplaces also transfers comments into rank col (dns,dnf)
        #      and orders the unfinished riders
        self.clearplaces()
        count = 0
        for cat in self.cats:
            ft = None
            if len(self.results[cat]) > 0:
                ft = self.results[cat][0][0]
            limit = None
            if ft is not None and self.timelimit is not None:
                limit = self.decode_limit(self.timelimit, ft)
                if limit is not None:
                    _log.info('Time limit: ' + self.timelimit + ' = ' +
                              limit.rawtime(0) + ', +' +
                              (limit - ft).rawtime(0))
            lt = None
            place = 1
            pcount = 0
            for t in self.results[cat]:
                np = strops.bibser2bibstr(t[0].refid, t[0].index)
                if np in placelist:
                    _log.error('Result for rider %r already in placelist', np)
                    # this is a bad fail - indicates duplicate category entry
                placelist.append(np)
                i = self.getiter(t[0].refid, t[0].index)
                if i is not None:
                    if lt is not None:
                        if lt != t[0]:
                            place = pcount + 1
                    if limit is not None and t[0] > limit:
                        self.riders.set_value(i, COL_PLACE, 'otl')
                        self.riders.set_value(i, COL_COMMENT, 'otl')
                    else:
                        self.riders.set_value(i, COL_PLACE, str(place))
                    j = self.riders.get_iter(count)
                    self.riders.swap(j, i)
                    count += 1
                    pcount += 1
                    lt = t[0]
                else:
                    _log.error('Extra result for rider %r', np)

        # check counts for racestat
        self.racestat = 'prerace'
        fullcnt = len(self.riders)
        placed = 0
        for r in self.riders:
            if r[COL_PLACE] and r[COL_PLACE] in ['dns', 'dnf', 'dsq']:
                r[COL_ETA] = None
            else:
                i = self.getiter(r[COL_BIB], r[COL_SERIES])
                r[COL_ETA] = self.geteta(i)
            if r[COL_PLACE]:
                placed += 1
        _log.debug('placed = ' + str(placed) + ', total = ' + str(fullcnt))
        if placed > 0:
            if placed < fullcnt:
                self.racestat = 'virtual'
            else:
                self.places = ' '.join(placelist)
                if self.timerstat == 'finished':
                    self.racestat = 'final'
                else:
                    self.racestat = 'provisional'
        _log.debug('Racestat set to: ' + repr(self.racestat))

        # pass two: compute any intermediates
        self.bonuses = {}  # bonuses are global to stage
        for c in self.tallys:  # points are grouped by tally
            self.points[c] = {}
        for c in self.contests:
            _log.debug('Assigning places for contest %r', c)
            self.assign_places(c)

        self.doannounce = True

    def get_placelist(self):
        """Return place list."""
        # assume this follows a place sorting.
        fp = None
        ret = ''
        for r in self.riders:
            if r[COL_PLACE]:
                #bibstr = strops.bibser2bibstr(r[COL_BIB], r[COL_SERIES])
                bibstr = r[COL_BIB]  # bibstr will fail later on
                if r[COL_PLACE] != fp:
                    ret += ' ' + bibstr
                else:
                    ret += '-' + bibstr
        return ret

    def get_starters(self):
        """Return a list of riders that 'started' the race."""
        ret = []
        for r in self.riders:
            if r[COL_COMMENT] != 'dns':
                ret.append(r[COL_BIB])
        return ' '.join(ret)

    def assign_places(self, contest):
        """Transfer points and bonuses into the named contest."""
        # fetch context meta infos
        src = self.contestmap[contest]['source']
        if src not in RESERVED_SOURCES and src not in self.intermeds:
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
            placestr = self.get_placelist()
            _log.info('Using placestr %r', placestr)
            if tally in ['sprint', 'crit']:  # really only for sprints/crits
                countbackwinner = True
        elif src == 'reg':
            placestr = self.get_startlist()
        elif src == 'start':
            placestr = self.get_starters()
        #elif src in self.catplaces:  # ERROR -> cat climb tally needs type?
        #placestr = self.get_cat_placesr(self.catplaces[src])
        #countbackwinner = True
        else:
            placestr = self.intermap[src]['places']
        placeset = set()
        idx = 0
        for placegroup in placestr.split():
            curplace = idx + 1
            for bib in placegroup.split('-'):
                if bib not in placeset:
                    placeset.add(bib)
                    b, s = strops.bibstr2bibser(bib)
                    r = self.getrider(b, s)
                    if r is None:
                        _log.error('Invalid rider %r ignored in %r places',
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
                            else:  # intermediate/other
                                if tally == 'climb':  # climbs countback on category winners only
                                    if curplace == 1:
                                        self.pointscb[tally][bib][
                                            category] += 1
                                else:
                                    self.pointscb[tally][bib][curplace] += 1
                else:
                    _log.warning('Duplicate no. %r in %r places', bib, contest)

    def getiter(self, bib, series=''):
        """Return temporary iterator to model row."""
        i = self.riders.get_iter_first()
        while i is not None:
            if self.riders.get_value(i,
                                     COL_BIB) == bib and self.riders.get_value(
                                         i, COL_SERIES) == series:
                break
            i = self.riders.iter_next(i)
        return i

    #def unstart(self, bib='', series='', wst=None):
    #"""Register a rider as not yet started."""
    #idx = strops.bibser2bibstr(bib, series)
    #self.unstarters[idx] = wst

    #def oncourse(self, bib='', series=''):
    #"""Remove rider from the not yet started list."""
    #pass
    #idx = strops.bibser2bibstr(bib, series)
    #if idx in self.unstarters:
    #del(self.unstarters[idx])

    def dnfriders(self, biblist='', code='dnf'):
        """Remove each rider from the race with supplied code."""
        recalc = False
        for bibstr in biblist.split():
            bib, ser = strops.bibstr2bibser(bibstr)
            r = self.getrider(bib, ser)
            if r is not None:
                r[COL_COMMENT] = code
                nri = self.getiter(bib, ser)
                self.settimes(nri, doplaces=False)
                recalc = True
            else:
                _log.warning('Unregistered Rider ' + str(bibstr) +
                             ' unchanged.')
        if recalc:
            self.placexfer()
        return False

    def setinter(self, iter, imed=None, inter=None):
        """Update the intermediate time for this rider and return rank."""
        bib = self.riders.get_value(iter, COL_BIB)
        series = self.riders.get_value(iter, COL_SERIES)
        cs = self.riders.get_value(iter, COL_CAT)
        cat = self.ridercat(riderdb.primary_cat(cs))
        ret = None

        # fetch handles
        res = self.inters[inter][cat]

        # clear result for this bib
        res.remove(bib, series)

        # save intermed tod to rider model
        self.riders.set_value(iter, inter, imed)
        tst = self.riders.get_value(iter, COL_TODSTART)
        wst = self.riders.get_value(iter, COL_WALLSTART)

        # determine start time
        if imed is not None:
            if tst is not None:  # got a start trigger
                res.insert(imed - tst, None, bib, series)
                ret = res.rank(bib, series)
            elif wst is not None:  # start on wall time
                res.insert(imed - wst, None, bib, series)
                ret = res.rank(bib, series)
            else:
                _log.error('No start time for intermediate ' +
                           strops.bibser2bibstr(bib, series))
        return ret

    def setpasses(self, iter, passes=None):
        """Set rider pass count."""
        self.riders.set_value(iter, COL_PASS, passes)

    def settimes(self,
                 iter,
                 wst=None,
                 tst=None,
                 tft=None,
                 pt=None,
                 doplaces=True):
        """Transfer race times into rider model."""
        bib = self.riders.get_value(iter, COL_BIB)
        series = self.riders.get_value(iter, COL_SERIES)
        cs = self.riders.get_value(iter, COL_CAT)
        cat = self.ridercat(riderdb.primary_cat(cs))
        #_log.debug('Check: ' + repr(bib) + ', ' + repr(series)
        #+ ', ' + repr(cat))

        # clear result for this bib
        self.results[cat].remove(bib, series)

        # assign tods
        if wst is not None:  # Don't clear a set wall start time!
            self.riders.set_value(iter, COL_WALLSTART, wst)
        else:
            wst = self.riders.get_value(iter, COL_WALLSTART)
        #self.unstart(bib, series, wst)	# reg ignorer
        # but allow others to be cleared no worries
        oft = self.riders.get_value(iter, COL_TODFINISH)
        self.riders.set_value(iter, COL_TODSTART, tst)
        self.riders.set_value(iter, COL_TODFINISH, tft)

        if pt is not None:  # Don't clear penalty either
            self.riders.set_value(iter, COL_TODPENALTY, pt)
        else:
            pt = self.riders.get_value(iter, COL_TODPENALTY)

        # save result
        if tft is not None:
            self.onestart = True
            if tst is not None:  # got a start trigger
                self.results[cat].insert(
                    (tft - tst).truncate(self.precision) + pt, None, bib,
                    series)
            elif wst is not None:  # start on wall time
                self.results[cat].insert(
                    (tft - wst).truncate(self.precision) + pt, None, bib,
                    series)
            else:
                _log.error('No start time for rider ' +
                           strops.bibser2bibstr(bib, series))
        elif tst is not None:
            #self.oncourse(bib, series)	# started but not finished
            pass

        # if reqd, do places
        if doplaces and oft != tft:
            self.placexfer()

    def bibent_cb(self, entry, tp):
        """Bib entry callback."""
        bib = tp.bibent.get_text().strip()
        series = tp.serent.get_text().strip()
        namestr = self.lanelookup(bib, series)
        if namestr is not None:
            tp.biblbl.set_text(self.lanelookup(bib, series))
            tp.toload()

    def tment_cb(self, entry, tp):
        """Manually register a finish time."""
        thetime = tod.mktod(entry.get_text())
        if thetime is not None:
            bib = tp.bibent.get_text().strip()
            series = tp.serent.get_text().strip()
            if bib != '':
                self.armfinish()
                self.meet.alttimer.trig(thetime, chan=1, index='MANU')
                entry.set_text('')
                tp.grab_focus()
        else:
            _log.error('Invalid finish time.')

    def lanelookup(self, bib=None, series=''):
        """Prepare name string for timer lane."""
        rtxt = None
        r = self.getrider(bib, series)
        if r is None:
            _log.info('Non starter specified: ' + repr(bib))
        else:
            rtxt = strops.truncpad(r[COL_NAMESTR], 35)
        return rtxt

    def time_context_menu(self, widget, event, data=None):
        """Popup menu for result list."""
        self.context_menu.popup(None, None, None, event.button, event.time,
                                selpath)

    def treeview_button_press(self, treeview, event):
        """Set callback for mouse press on model view."""
        if event.button == 3:
            pathinfo = treeview.get_path_at_pos(int(event.x), int(event.y))
            if pathinfo is not None:
                path, col, cellx, celly = pathinfo
                treeview.grab_focus()
                treeview.set_cursor(path, col, 0)
                self.context_menu.popup(None, None, None, event.button,
                                        event.time)
                return True
        return False

    def tod_context_print_activate_cb(self, menuitem, data=None):
        """Print times for selected rider."""
        _log.info('Print times not implemented.')
        pass

    def tod_context_dns_activate_cb(self, menuitem, data=None):
        """Register rider as non-starter."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]  # grab off row iter
            bib = self.riders.get_value(i, COL_BIB)
            series = self.riders.get_value(i, COL_SERIES)
            self.dnfriders(strops.bibser2bibstr(bib, series), 'dns')

    def tod_context_dnf_activate_cb(self, menuitem, data=None):
        """Register rider as non-finisher."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]  # grab off row iter
            bib = self.riders.get_value(i, COL_BIB)
            series = self.riders.get_value(i, COL_SERIES)
            self.dnfriders(strops.bibser2bibstr(bib, series), 'dnf')

    def tod_context_dsq_activate_cb(self, menuitem, data=None):
        """Disqualify rider."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]  # grab off row iter
            bib = self.riders.get_value(i, COL_BIB)
            series = self.riders.get_value(i, COL_SERIES)
            self.dnfriders(strops.bibser2bibstr(bib, series), 'dsq')

    def tod_context_rel_activate_cb(self, menuitem, data=None):
        """Relegate rider."""
        _log.info('Relegate not implemented for time trial.')
        pass

    def tod_context_ntr_activate_cb(self, menuitem, data=None):
        """Register no time recorded for rider and place last."""
        ## TODO
        _log.info('NTR not implemented for time trial.')
        pass

    def tod_context_clear_activate_cb(self, menuitem, data=None):
        """Clear times for selected rider."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            self.riders.set_value(sel[1], COL_COMMENT, '')
            self.riders.set_value(sel[1], COL_PASS, 0)
            self.settimes(sel[1])  # clear iter to empty vals
            self.log_clear(self.riders.get_value(sel[1], COL_BIB),
                           self.riders.get_value(sel[1], COL_SERIES))

    def now_button_clicked_cb(self, button, entry=None):
        """Set specified entry to the current time."""
        if entry is not None:
            entry.set_text(tod.now().timestr())

    def tod_context_edit_activate_cb(self, menuitem, data=None):
        """Run edit time dialog."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]  # grab off row iter and read in cur times
            tst = self.riders.get_value(i, COL_TODSTART)
            tft = self.riders.get_value(i, COL_TODFINISH)
            tpt = self.riders.get_value(i, COL_TODPENALTY)

            # prepare text entry boxes
            st = ''
            if tst is not None:
                st = tst.timestr()
            ft = ''
            if tft is not None:
                ft = tft.timestr()
            bt = ''
            pt = '0'
            if tpt is not None:
                pt = tpt.timestr()

            # run the dialog
            (ret, st, ft, bt, pt) = uiutil.edit_times_dlg(self.meet.window,
                                                          st,
                                                          ft,
                                                          bt,
                                                          pt,
                                                          bonus=False,
                                                          penalty=True)
            if ret == 1:
                stod = tod.mktod(st)
                ftod = tod.mktod(ft)
                ptod = tod.mktod(pt)
                if ptod is None:
                    ptod = tod.ZERO
                bib = self.riders.get_value(i, COL_BIB)
                series = self.riders.get_value(i, COL_SERIES)
                self.settimes(i, tst=stod, tft=ftod, pt=ptod)  # update model
                _log.info('Race times manually adjusted for rider ' +
                          strops.bibser2bibstr(bib, series))
            else:
                _log.info('Edit race times cancelled.')

    def tod_context_del_activate_cb(self, menuitem, data=None):
        """Delete selected row from race model."""
        sel = self.view.get_selection().get_selected()
        if sel is not None:
            i = sel[1]  # grab off row iter
            self.settimes(i)  # clear times
            if self.riders.remove(i):
                pass  # re-select?

    def log_clear(self, bib, series):
        """Print clear time log."""
        _log.info('Time cleared for rider ' +
                  strops.bibser2bibstr(bib, series))

    def title_close_clicked_cb(self, button, entry=None):
        """Close and save the race."""
        self.meet.close_event()

    def set_titlestr(self, titlestr=None):
        """Update the title string label."""
        if titlestr is None or titlestr == '':
            titlestr = 'Individual Road Time Trial'
        self.title_namestr.set_text(titlestr)

    def destroy(self):
        """Signal race shutdown."""
        if self.context_menu is not None:
            self.context_menu.destroy()
        self.frame.destroy()

    def show(self):
        """Show race window."""
        self.frame.show()

    def hide(self):
        """Hide race window."""
        self.frame.hide()

    def ridercat(self, cat):
        """Return a category from the result for the riders cat."""
        ret = ''  # default is the 'None' category - uncategorised
        checka = cat.upper()
        if checka in self.results:
            ret = checka
        #_log.debug('ridercat read ' + repr(cat) + '/' + repr(checka)
        #+ '  Returned: ' + repr(ret))
        return ret

    def get_catlist(self):
        """Return the ordered list of categories."""
        rvec = []
        for cat in self.cats:
            if cat != '':
                rvec.append(cat)
        return rvec

    def __init__(self, meet, event, ui=True):
        """Constructor."""
        self.meet = meet
        self.event = event
        self.evno = event['evid']
        # series is specified per-rider
        self.configfile = meet.event_configfile(self.evno)
        self.readonly = not ui
        rstr = ''
        if self.readonly:
            rstr = 'readonly '
        _log.debug('Init %r event %r', rstr, self.evno)

        # properties
        self.sloppystart = False
        self.sloppyimpulse = False
        self.autoexport = False
        self.finishloop = None
        self.startloop = None
        self.starttrig = None
        self.precision = 2
        self.finishpass = None
        self.hidetimers = False

        # race run time attributes
        self.onestart = False
        self.winopen = True
        self.timerstat = 'idle'
        self.racestat = 'prerace'
        self.showuciids = False
        self.start = None
        self.lstart = None
        self.start_unload = None
        self.startgap = None
        self.startdelay = tod.ZERO
        self.cats = []  # the ordered list of cats for results
        self.autocats = False
        self.startpasses = tod.todlist('start')
        self.finishpasses = tod.todlist('finish')
        self.results = {'': tod.todlist('UNCAT')}
        self.inters = {}
        self.ischem = {}
        self.showinter = None
        for im in [COL_INTERA, COL_INTERB, COL_INTERC, COL_INTERD, COL_INTERE]:
            self.inters[im] = {'': tod.todlist('UNCAT')}
            self.ischem[im] = None
        self.interloops = {}  # map of loop ids to inter splits
        self.curfintod = None
        self.doannounce = False
        self.onestartlist = False
        self.curcat = ''
        self.catlaps = {}
        self.comment = []
        self.places = ''

        self.bonuses = {}
        self.points = {}
        self.pointscb = {}

        ## these have to go!
        self.intermeds = []  # sorted list of intermediate keys
        self.intermap = {}  # map of intermediate keys to results
        self.contests = []  # sorted list of contests
        self.contestmap = {}  # map of contest keys
        self.tallys = []  # sorted list of points tallys
        self.tallymap = {}  # map of tally keys

        self.riders = Gtk.ListStore(
            str,  # gobject.TYPE_STRING,  # 0 bib
            str,  # gobject.TYPE_STRING,  # 1 series
            str,  # gobject.TYPE_STRING,  # 2 namestr
            str,  # gobject.TYPE_STRING,  # 3 cat
            str,  # gobject.TYPE_STRING,  # 4 comment
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 5 wstart
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 6 tstart
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 7 finish
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 8 penalty
            str,  # gobject.TYPE_STRING,  # 9 place
            str,  # gobject.TYPE_STRING,  # 10 shortname
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 11 intermediate
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 12 intermediate
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 13 intermediate
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 14 intermediate
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 15 intermediate
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 16 last seen
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 17 eta
            int,  # gobject.TYPE_INT,  # 18 pass count
            tod.tod,  # gobject.TYPE_PYOBJECT,  # 19 stage bonus
            tod.
            tod,  # gobject.TYPE_PYOBJECT  # 20 stage penalty (sep to time pen)
        )

        b = uiutil.builder('irtt.ui')
        self.frame = b.get_object('race_vbox')
        self.frame.connect('destroy', self.shutdown)

        # meta info pane
        self.title_namestr = b.get_object('title_namestr')
        self.set_titlestr()

        # Timer Panes
        mf = b.get_object('race_timer_pane')
        self.sl = uiutil.timerpane('Start Line', doser=True)
        self.sl.disable()
        self.sl.bibent.connect('activate', self.bibent_cb, self.sl)
        self.sl.serent.connect('activate', self.bibent_cb, self.sl)
        self.fl = uiutil.timerpane('Finish Line', doser=True)
        self.fl.disable()
        self.fl.bibent.connect('activate', self.bibent_cb, self.fl)
        self.fl.serent.connect('activate', self.bibent_cb, self.fl)
        self.fl.tment.connect('activate', self.tment_cb, self.fl)
        mf.pack_start(self.sl.frame)
        mf.pack_start(self.fl.frame)
        mf.set_focus_chain([self.sl.frame, self.fl.frame, self.sl.frame])
        self.timerframe = mf

        # Result Pane
        t = Gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(True)
        t.set_rules_hint(True)
        self.context_menu = None

        # show window
        if ui:
            t.connect('button_press_event', self.treeview_button_press)
            # TODO: show team name & club but pop up for rider list
            uiutil.mkviewcolbibser(t)
            uiutil.mkviewcoltxt(t, 'Rider', COL_NAMESTR, expand=True)
            uiutil.mkviewcoltxt(t, 'Cat', COL_CAT, self.editcol_cb)
            uiutil.mkviewcoltxt(t, 'Passes', COL_PASS, self.editcol_cb)
            # -> Add in start time field with edit!
            uiutil.mkviewcoltod(t, 'Start', cb=self.wallstartstr)
            uiutil.mkviewcoltod(t, 'Time', cb=self.elapstr)
            uiutil.mkviewcoltxt(t, 'Rank', COL_PLACE, halign=0.5, calign=0.5)
            t.show()
            b.get_object('race_result_win').add(t)
            b.connect_signals(self)

            b = uiutil.builder('tod_context.ui')
            self.context_menu = b.get_object('tod_context')
            b.connect_signals(self)

            # reconfigure the chronometer
            self.meet.alttimer.armlock()  # lock the arm to capture all hits
            self.meet.alttimer.arm(0)  # start line
            self.meet.alttimer.arm(1)  # finish line (primary)
            self.meet.alttimer.arm(2)  # use for backup trigger a
            self.meet.alttimer.arm(3)  # use for backup trigger b
            self.meet.alttimer.delaytime('0.01')

            # connect timer callback functions
            self.meet.timercb = self.timertrig  # transponders
            self.meet.alttimercb = self.alttimertrig  # chronometer
