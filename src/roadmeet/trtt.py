# SPDX-License-Identifier: MIT
"""Road team time time trial."""

#import os
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
from metarace import report
from metarace import jsonconfig
from . import uiutil

from roadmeet.rms import rms

_log = logging.getLogger('metarace.trtt')
_log.setLevel(logging.DEBUG)

# trtt adds team reference to rms model
# basic infos
COL_BIB = 0
COL_NAMESTR = 1
COL_SHORTNAME = 2
COL_CAT = 3
COL_COMMENT = 4
COL_INRACE = 5  # boolean in the race
COL_PLACE = 6  # Place assigned in result
COL_LAPS = 7  # Incremented if inrace and not finished
COL_SEED = 8  # Seeding number (overrides startlist ordering)

# timing infos
COL_RFTIME = 9  # one-off finish time by rfid
COL_CBUNCH = 10  # computed bunch time   -> derived from rftime
COL_MBUNCH = 11  # manual bunch time     -> manual overrive
COL_STOFT = 12  # start time 'offset' - only reported in result
COL_BONUS = 13
COL_PENALTY = 14
COL_RFSEEN = 15  # list of tods this rider 'seen' by rfid

# team reference column
COL_TEAM = 16  # stored team ref for quick refs

# Nth wheel decides whose time is counted to the team
NTH_WHEEL = 3

# Minimum lap/elap time, should be at least the same as start gaps
MINLAP = tod.tod('2:00')

# Add a gap in the startlist when gap is larger than TEAMGAP
TEAMGAP = tod.tod('4:00')

# Default time gap threshold
GAPTHRESH = tod.tod('1.12')

# config version string
EVENT_ID = 'trtt-3.0'


class trtt(rms):

    def team_start_times(self):
        """Scan riders and patch start times from team entry."""
        self.teamnames = {}
        self.teamuci = {}
        teamstarts = {}
        # pass 1: extract team times and names
        for r in self.riders:
            nt = r[COL_TEAM]
            if nt not in teamstarts:
                teamUci = ''
                teamName = nt
                st = tod.ZERO
                dbr = self.meet.rdb.get_rider(nt, 'team')
                if dbr is not None:
                    st = tod.mktod(dbr['refid'])
                    teamName = dbr['first']
                    teamUci = dbr['uci id']
                else:
                    _log.warning('No team entry found for %r (rider: %s)', nt,
                                 r[COL_BIB])
                self.teamnames[nt] = teamName
                self.teamuci[nt] = teamUci
                teamstarts[nt] = st

        # pass 2: patch start times if present
        cnt = 0
        for r in self.riders:
            nt = r[COL_TEAM]
            if nt in teamstarts and teamstarts[nt]:
                r[COL_STOFT] = teamstarts[nt]
                cnt += 1
            else:
                r[COL_STOFT] = tod.ZERO
                _log.warning('No start time for %s:%s', r[COL_TEAM],
                             r[COL_BIB])
        _log.debug('Patched %r start times', cnt)

    def loadconfig(self):
        """Load event config from disk."""
        self.riders.clear()
        self.resettimer()
        self.cats = []

        cr = jsonconfig.config({
            'trtt': {
                'start': None,
                'id': EVENT_ID,
                'finished': False,
                'showuciids': False,
                'relativestart': False,
                'showriders': True,
                'places': '',
                'comment': [],
                'categories': [],
                'intermeds': [],
                'allowspares': False,
                'contests': [],
                'tallys': [],
                'owntime': True,
                'totlaps': None,
                'passingsource': [],
                'defaultnth': NTH_WHEEL,
                'minlap': None,
                'nthwheel': {},
                'startlist': '',
                'resultcats': False,
                'autofinish': False,
                'autoexport': False,
            }
        })
        cr.add_section('trtt')
        cr.add_section('riders')
        cr.add_section('stagebonus')
        cr.add_section('stagepenalty')
        cr.merge(metarace.sysconf, 'trtt')
        if not cr.load(self.configfile):
            _log.info('%r not read, loading defaults', self.configfile)

        # load result categories
        self.loadcats(cr.get('trtt', 'categories'))

        # read in default and category specific nth wheel values
        self.defaultnth = cr.get_posint('trtt', 'defaultnth')
        _log.debug('Default Nth Wheel: %r', self.defaultnth)
        self.nthwheel = cr.get('trtt', 'nthwheel')
        if not isinstance(self.nthwheel, dict):
            _log.warning('Invalid nthwheel setting ignored: %r', self.nthwheel)
            self.nthwheel = {}
        if len(self.nthwheel) > 0:
            _log.debug('Nth Wheel: %r', self.nthwheel)

        # amend reserved sources with any cats
        if len(self.cats) > 1:
            for cat in self.cats:
                if cat:
                    srcid = cat.lower() + 'fin'
                    RESERVED_SOURCES.append(srcid)
                    self.catplaces[srcid] = cat
        self.passingsource = []
        for source in cr.get('trtt', 'passingsource'):
            self.passingsource.append(source.lower())  # force lower case

        # restore stage inters, points and bonuses
        self.loadstageinters(cr, 'trtt')

        # load competitors
        starters = cr.get('trtt', 'startlist').split()
        if len(starters) == 1 and starters[0] == 'all':
            starters = strops.riderlist_split('all', self.meet.rdb)
        self.allowspares = cr.get_bool('trtt', 'allowspares')
        for r in starters:
            self.addrider(r)
            if cr.has_option('riders', r):
                nr = self.getrider(r)
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
                    nr[COL_RFTIME] = tod.mktod(ril[3])
                if lr > 4:
                    nr[COL_MBUNCH] = tod.mktod(ril[4])
                if lr > 5:
                    nr[COL_STOFT] = tod.mktod(ril[5])
                if lr > 6:
                    for i in range(6, lr):
                        laptod = tod.mktod(ril[i])
                        if laptod is not None:
                            nr[COL_RFSEEN].append(laptod)
            # record any extra bonus/penalty to rider model
            if cr.has_option('stagebonus', r):
                nr[COL_BONUS] = tod.mktod(cr.get('stagebonus', r))
            if cr.has_option('stagepenalty', r):
                nr[COL_PENALTY] = tod.mktod(cr.get('stagepenalty', r))

        self.owntime = cr.get_bool('trtt', 'owntime')
        self.minlap = tod.mktod(cr.get('trtt', 'minlap'))
        if self.minlap is None:
            self.minlap = MINLAP
        _log.debug('Minimum lap time: %s', self.minlap.rawtime())

        self.set_start(cr.get('trtt', 'start'))
        self.totlaps = cr.get('trtt', 'totlaps')
        self.places = strops.reformat_placelist(cr.get('trtt', 'places'))
        self.comment = cr.get('trtt', 'comment')
        self.autoexport = cr.get_bool('trtt', 'autoexport')
        self.showuciids = cr.get_bool('trtt', 'showuciids')
        self.showriders = cr.get_bool('trtt', 'showriders')
        self.relativestart = cr.get_bool('trtt', 'relativestart')
        if strops.confopt_bool(cr.get('trtt', 'finished')):
            self.set_finished()
        self.recalculate()

        self.load_cat_data()

        if self.totlaps is not None:
            self.totlapentry.set_text(str(self.totlaps))

        # patch team start times from riderdb
        self.team_start_times()

        if cr.get_bool('trtt', 'autofinish'):
            # then override targetlaps if autofinish was set
            self.targetlaps = True

        # After load complete - check config and report.
        eid = cr.get('trtt', 'id')
        if eid and eid != EVENT_ID:
            _log.info('Event config mismatch: %r != %r', eid, EVENT_ID)
            self.readonly = True

    def load_cat_data(self):
        self.catlaps = {}
        onetarget = False
        onemissing = False
        for c in self.cats:
            ls = None
            # fetch data on all but the uncat cat
            if c:
                dbr = self.meet.rdb.get_rider(c, 'cat')
                if dbr is not None:
                    lt = strops.confopt_posint(dbr['target laps'])
                    if lt:
                        ls = lt
                        onetarget = True
                    else:
                        onemissing = True
            self.catlaps[c] = ls
        if onetarget:
            self.targetlaps = True
            if onemissing:
                # There's one or more cats without a target, issue warning
                missing = []
                for c in self.catlaps:
                    if self.catlaps[c] is None:
                        missing.append(repr(c))
                if missing:
                    _log.warning('Categories missing target lap count: %s',
                                 ', '.join(missing))
            _log.debug('Category laps: %r', self.catlaps)

    def saveconfig(self):
        """Save event config to disk."""
        if self.readonly:
            _log.error('Attempt to save readonly event')
            return
        cw = jsonconfig.config()
        cw.add_section('trtt')
        if self.start is not None:
            cw.set('trtt', 'start', self.start.rawtime())
        if self.minlap is not None:
            cw.set('trtt', 'minlap', self.minlap.rawtime())
        else:
            cw.set('trtt', 'minlap', None)
        cw.set('trtt', 'showuciids', self.showuciids)
        cw.set('trtt', 'showriders', self.showriders)
        cw.set('trtt', 'relativestart', self.relativestart)
        cw.set('trtt', 'finished', self.timerstat == 'finished')
        cw.set('trtt', 'places', self.places)
        cw.set('trtt', 'totlaps', self.totlaps)
        cw.set('trtt', 'allowspares', self.allowspares)
        cw.set('trtt', 'defaultnth', self.defaultnth)
        cw.set('trtt', 'owntime', self.owntime)
        cw.set('trtt', 'autofinish', self.targetlaps)
        cw.set('trtt', 'autoexport', self.autoexport)
        cw.set('trtt', 'passingsource', self.passingsource)
        cw.set('trtt', 'clubmode', self.clubmode)
        cw.set('trtt', 'nthwheel', self.nthwheel)  # dict of cat keys

        # save stage inters, points and bonuses
        self.savestageinters(cw, 'trtt')

        # save riders
        evtriders = self.get_startlist()
        if evtriders:
            cw.set('trtt', 'startlist', self.get_startlist())
        else:
            if self.autostartlist is not None:
                cw.set('trtt', 'startlist', self.autostartlist)
        if self.autocats:
            cw.set('trtt', 'categories', ['AUTO'])
        else:
            cw.set('trtt', 'categories', self.get_catlist())
        cw.set('trtt', 'comment', self.comment)

        cw.add_section('riders')
        # sections for commissaire awarded bonus/penalty
        cw.add_section('stagebonus')
        cw.add_section('stagepenalty')
        for r in self.riders:
            rt = ''
            if r[COL_RFTIME] is not None:
                rt = r[COL_RFTIME].rawtime()
            mb = ''
            if r[COL_MBUNCH] is not None:
                mb = r[COL_MBUNCH].rawtime(1)
            sto = ''
            if r[COL_STOFT] is not None:
                sto = r[COL_STOFT].rawtime()
            # bib = comment,in,laps,rftod,mbunch,rfseen...
            bib = r[COL_BIB]
            slice = [r[COL_COMMENT], r[COL_INRACE], r[COL_LAPS], rt, mb, sto]
            for t in r[COL_RFSEEN]:
                if t is not None:
                    slice.append(t.rawtime())
            cw.set('riders', bib, slice)
            if r[COL_BONUS] is not None:
                cw.set('stagebonus', bib, r[COL_BONUS].rawtime())
            if r[COL_PENALTY] is not None:
                cw.set('stagepenalty', bib, r[COL_PENALTY].rawtime())
        cw.set('trtt', 'id', EVENT_ID)
        _log.debug('Saving event config %r', self.configfile)
        with metarace.savefile(self.configfile) as f:
            cw.write(f)

    def set_titlestr(self, titlestr=None):
        """Update the title string label."""
        if titlestr is None or titlestr == '':
            titlestr = '[Road Team Time Trial]'
        self.title_namestr.set_text(titlestr)

    def reorder_riderno(self):
        """Reorder riders by riderno."""
        self.calcset = False
        aux = []
        cnt = 0
        for r in self.riders:
            aux.append((strops.riderno_key(r[COL_BIB]), cnt))
            cnt += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[1] for a in aux])
        return cnt

    def reorder_startlist(self):
        """Reorder riders for a startlist."""
        self.calcset = False
        aux = []
        cnt = 0
        for r in self.riders:
            aux.append((r[COL_STOFT], strops.riderno_key(r[COL_BIB]), cnt))
            cnt += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[2] for a in aux])
        return cnt

    def callup_report(self):
        """Callup is startlist in this case"""
        return self.startlist_report()

    def startlist_report(self):
        """Return a startlist report."""
        # This is time trial - so generate a time specific startlist
        ret = []
        cnt = self.reorder_startlist()
        tcount = 0
        rcount = 0
        sec = None
        lcat = None
        ltod = None
        lteam = None
        for r in self.riders:
            rcount += 1
            rno = r[COL_BIB]
            rname = r[COL_SHORTNAME]
            rteam = r[COL_TEAM]
            rstart = r[COL_STOFT]
            ruci = None
            tuci = None
            if rstart is None:
                rstart = tod.MAX
            if rteam != lteam:  # issue team time
                ltod = None
                cs = r[COL_CAT]
                tcat = self.ridercat(riderdb.primary_cat(cs))
                if self.showuciids:
                    dbr = self.meet.rdb.get_rider(rteam, 'team')
                    if dbr is not None:
                        tuci = dbr['uci id']
                if not tuci and tcat == '':
                    tuci = cs
                if lcat != tcat:
                    tcount = 0
                    catname = ''
                    if sec is not None:
                        ret.append(sec)
                        pb = report.pagebreak()
                        ##pb.set_threshold(0.60)
                        ret.append(pb)
                    sec = report.rttstartlist('startlist')
                    sec.heading = 'Startlist'
                    dbr = self.meet.rdb.get_rider(tcat, 'cat')
                    if dbr is not None:
                        catname = dbr['title']
                        if catname:
                            sec.heading += ' - ' + catname
                        subhead = dbr['subtitle']
                        if subhead:
                            sec.subheading = subhead
                        footer = dbr['footer']
                        if footer:
                            sec.footer = footer
                lcat = tcat

                tname = rteam  # use key and only replace if avail
                if rteam in self.teamnames:
                    tname = self.teamnames[rteam]
                if ltod is not None and rstart - ltod > TEAMGAP:
                    sec.lines.append([])
                ltod = rstart
                cstr = ''
                tcount += 1
                tcodestr = rteam.upper()
                if rteam.isdigit():
                    tcodestr = None
                startStr = rstart.meridiem()
                if self.relativestart:
                    startStr = rstart.rawtime(0)
                sec.lines.append(
                    [startStr, tcodestr, tname, tuci, '___', cstr])
                lteam = rteam
            if self.showriders:
                if self.showuciids:
                    dbr = self.meet.rdb.get_rider(rno, self.series)
                    if dbr is not None:
                        ruci = dbr['uci id']
                sec.lines.append([None, rno, rname, ruci, None, None, None])
        ret.append(sec)
        return ret

    def reorder_arrivals(self):
        """Re-order the rider list according to arrival at finish line"""
        self.calcset = False
        aux = []
        cnt = 0
        for r in self.riders:
            # in the race?
            inField = True
            if not r[COL_INRACE]:
                inField = False

            comStr = r[COL_COMMENT]
            if r[COL_PLACE]:
                comStr = r[COL_PLACE]

            # assigned ranking
            rank = strops.dnfcode_key(comStr)

            # arrival at finish line
            arrivalTime = tod.MAX
            if inField and r[COL_RFTIME] is not None:
                arrivalTime = r[COL_RFTIME]

            # count of non-finish passings (reversed)
            lapCount = 0
            if inField:
                lapCount = -r[COL_LAPS]

            # last seen passing
            lastSeen = tod.MAX
            if inField and len(r[COL_RFSEEN]) > 0:
                lastSeen = r[COL_RFSEEN][-1]

            # team start time
            teamStart = tod.MAX
            if inField and r[COL_STOFT] is not None:
                teamStart = r[COL_STOFT]

            # rider number key
            riderNo = strops.riderno_key(r[COL_BIB])

            aux.append((rank, arrivalTime, lapCount, lastSeen, teamStart,
                        riderNo, cnt))
            cnt += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[6] for a in aux])
        return cnt

    def camera_report(self):
        """Return the judges (camera) report."""
        # Note: camera report treats all riders as a single blob
        ret = []
        self.recalculate()  # fill places and bunch info, order by arrival
        pthresh = self.meet.timer.photothresh()
        totcount = 0
        dnscount = 0
        dnfcount = 0
        fincount = 0
        lcomment = ''
        insertgap = True
        teamCount = {}
        teamFirstWheel = {}
        if self.timerstat != 'idle':
            sec = report.judgerep('judging')
            sec.heading = 'Judges Report'
            sec.colheader = [
                'hit', 'team', 'rider', 'lap', 'time', 'arrival', 'passings'
            ]
            repStart = tod.ZERO
            sec.start = repStart
            if self.start is not None:
                repStart = self.start
            sec.finish = tod.tod('5.0')
            laptimes = (tod.tod('0.5'), tod.tod('1.5'), tod.tod('2.5'),
                        tod.tod('3.5'), tod.tod('4.5'))
            sec.laptimes = laptimes
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
                catstart = None
                catfinish = None
                chevron = None
                rteam = r[COL_TEAM]
                rname = r[COL_SHORTNAME]
                rbib = r[COL_BIB]
                rid = ' '.join((rbib, rname))
                rcat = r[COL_CAT]
                ecat = self.ridercat(riderdb.primary_cat(rcat))
                laplist = []
                if r[COL_RFTIME] is not None:
                    if rteam not in teamFirstWheel:
                        # first arrival for this team
                        teamFirstWheel[rteam] = r[COL_RFTIME] - tod.tod('0.5')
                        teamCount[rteam] = 0
                    teamCount[rteam] += 1
                    catstart = teamFirstWheel[rteam]
                    catfinish = catstart + tod.tod('5.0')
                    for lt in r[COL_RFSEEN]:
                        if lt <= r[COL_RFTIME]:
                            laplist.append(lt)
                    _log.debug(
                        'rider: %r, laptimes: %r, laplist: %r, catstart: %r, catfinish: %r',
                        rbib, laptimes, laplist, catstart, catfinish)
                else:
                    # include all captured laps
                    laplist = r[COL_RFSEEN]
                if r[COL_INRACE]:
                    if r[COL_RFTIME] is not None:
                        timed = True
                    comment = str(totcount)
                    bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if timed or bt is not None:
                        fincount += 1
                        if r[COL_PLACE] != '':
                            # in trtt this is arrival
                            #comment = r[COL_PLACE] + u'.'
                            placed = True
                            pset = True

                        # format arrival time
                        if r[COL_RFTIME] is not None:
                            if not pset and lrf is not None:
                                if r[COL_RFTIME] - lrf < pthresh:
                                    photo = True
                                    if not sec.lines[-1][7]:  # not placed
                                        sec.lines[-1][8] = True
                            rstart = r[COL_STOFT] + repStart
                            es = r[COL_RFTIME].rawtime(1)
                            et = r[COL_RFTIME] - rstart
                            # elapsed string
                            #es = et.rawtime(1)
                            lrf = r[COL_RFTIME]
                        else:
                            lrf = None

                        # format 'finish' time
                        bs = ''
                        if bt is not None:
                            if bt != self.teamtimes[rteam]:
                                chevron = '\u21CE'
                                bs = bt.rawtime(1)
                            elif teamCount[rteam] < 10:
                                chevron = str(teamCount[rteam]) + '\u20DD'
                                if rteam in self.teamnth:
                                    if teamCount[rteam] == self.teamnth[rteam]:
                                        bs = bt.rawtime(1)
                        # sep placed and unplaced
                        insertgap = False
                        if lplaced and placed != lplaced:
                            sec.lines.append([None, None, None])
                            sec.lines.append(
                                [None, None, 'Riders not yet confirmed'])
                            insertgap = True
                        lplaced = placed
                    else:
                        if r[COL_COMMENT].strip() != '':
                            comment = r[COL_COMMENT].strip()
                        else:
                            comment = '___'

                    # sep timed and untimed
                    if not insertgap and ltimed and ltimed != timed:
                        sec.lines.append([None, None, None])
                        sec.lines.append(
                            [None, None, 'Riders not seen at finish.'])
                        insertgap = True
                    ltimed = timed
                    sec.lines.append([
                        comment, rteam, rid,
                        str(r[COL_LAPS]), bs, es, laplist, placed, photo,
                        catstart, rcat, catfinish, chevron
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
                        comment, rteam, rid,
                        str(r[COL_LAPS]), None, es, laplist, True, False,
                        catstart, rcat, catfinish
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

    def single_catresult(self, cat):
        _log.debug('Cat result for cat=%r', cat)
        ret = []
        allin = False
        catname = cat  # fallback emergency
        if cat == '':
            if len(self.cats) > 1:
                catname = 'Uncategorised Riders'
            else:
                # There is only one cat - so all riders are in it
                allin = True
        subhead = ''
        footer = ''
        distance = self.meet.get_distance()
        laps = self.totlaps
        if self.catlaps[cat] is not None:
            laps = self.catlaps[cat]
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
        sec = report.section('result-' + cat)

        teamRes = {}
        teamAux = []
        teamCnt = 0
        finCnt = 0

        # find all teams and riders in the chosen cat
        for r in self.riders:
            rcat = r[COL_CAT].upper()
            rcats = ['']
            if rcat.strip():
                rcats = rcat.split()
            incat = False
            if allin or (cat and cat in rcats):
                incat = True  # rider is in this category
            elif not cat:  # is the rider uncategorised?
                incat = rcats[0] not in self.cats  # backward logic
            if incat:
                rteam = r[COL_TEAM]
                if rteam not in teamRes:
                    teamCnt += 1
                    teamRes[rteam] = {}
                    teamRes[rteam]['time'] = None
                    teamRes[rteam]['rlines'] = []
                    if rteam in self.teamtimes:
                        # this team has a finish time
                        finCnt += 1
                        auxTime = self.teamtimes[rteam]
                        tUci = ''
                        if self.showuciids:
                            tUci = self.teamuci[rteam]
                        teamAux.append((auxTime, teamCnt, rteam))
                        teamRes[rteam]['time'] = auxTime
                        teamRes[rteam]['tline'] = [
                            None, rteam, self.teamnames[rteam], tUci,
                            auxTime.rawtime(1), ''
                        ]
                rTime = ''
                rName = r[COL_SHORTNAME]
                rBib = r[COL_BIB]
                rCom = ''
                if r[COL_INRACE]:
                    if teamRes[rteam]['time'] is not None:
                        bt = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                        if bt is not None and bt != teamRes[rteam]['time']:
                            rDown = bt - teamRes[rteam]['time']
                            rTime = '[+' + rDown.rawtime(1) + ']'
                else:
                    rCom = r[COL_COMMENT]
                rUci = ''
                if self.showuciids:
                    dbr = self.meet.rdb.get_rider(rBib, self.series)
                    if dbr is not None:
                        rUci = dbr['uci id']
                teamRes[rteam]['rlines'].append(
                    [rCom, rBib, rName, rUci, rTime, ''])

        # sort, patch ranks and append result section
        teamAux.sort()
        first = True
        wt = None
        lt = None
        curPlace = 1
        tcnt = 0
        for t in teamAux:
            tcnt += 1
            team = t[2]
            teamTime = t[0]
            if teamTime != lt:
                curPlace = tcnt
            teamRank = str(curPlace) + '.'
            downStr = ''
            if wt is None:
                wt = teamTime
            else:
                downStr = '+' + (teamTime - wt).rawtime(1)
            teamRes[team]['tline'][0] = teamRank
            teamRes[team]['tline'][5] = downStr

            if self.showriders:
                if not first:
                    sec.lines.append([])
            first = False
            sec.lines.append(teamRes[team]['tline'])
            if self.showriders:
                sec.lines.extend(teamRes[team]['rlines'])

            lt = teamTime

        if self.timerstat == 'finished':
            sec.heading = 'Result'
        elif self.timerstat in ['idle', 'armstart']:
            sec.heading = ''
        elif self.timerstat in ['running', 'armfinish']:
            if teamCnt == finCnt:
                sec.heading = 'Provisional Result'
            elif finCnt > 0:
                sec.heading = 'Virtual Standing'
            else:
                sec.heading = 'Event in Progress'
        else:
            sec.heading = 'Provisional Result'
        if footer:
            sec.footer = footer

        # Append all result categories and uncat if riders
        if cat or finCnt > 0:
            ret.append(sec)
            rsec = sec
            # Race metadata / UCI comments
            sec = report.bullet_text('uci-' + cat)
            if wt is not None:
                if distance is not None:
                    sec.lines.append([
                        None, 'Average speed of the winner: ' +
                        wt.speedstr(1000.0 * distance)
                    ])
            sec.lines.append([None, 'Number of teams: ' + str(teamCnt)])
            #if dnfcount > 0:
            #sec.lines.append([
            #None,
            #u'Teams not completing the event: ' + unicode(dnfcount)
            #])
            #residual = totcount - (fincount + dnfcount + dnscount + hdcount)
            #if residual > 0:
            #_log.info(u'%r teams unaccounted for: %r', cat, residual)
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
        """Return a race result report"""
        return self.catresult_report()

    def race_ctrl_add(self, rlist):
        """Add the supplied riders to event model with lookup"""
        rlist = strops.riderlist_split(rlist, self.meet.rdb, self.series)
        for bib in rlist:
            self.addrider(bib)
        self.team_start_times()
        return True

    def race_ctrl_del(self, rlist):
        """Delete nominated riders from event model"""
        rlist = strops.riderlist_split(rlist, self.meet.rdb, self.series)
        for bib in rlist:
            self.delrider(bib)
        self.team_start_times()
        return True

    def startlist_gen(self, cat=''):
        """Generator function to export a startlist."""
        mcat = self.ridercat(cat)
        self.reorder_startlist()
        eventStart = tod.ZERO
        if self.start is not None:
            eventStart = self.start
        # in TTT cat start offsets are ignored
        for r in self.riders:
            cs = r[COL_CAT]
            rcat = self.ridercat(riderdb.primary_cat(cs))
            if mcat == rcat:
                start = ''
                if r[COL_STOFT] is not None and r[COL_STOFT] != tod.ZERO:
                    start = (eventStart + r[COL_STOFT]).rawtime(0)
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

    def result_gen(self, cat=''):
        """Generator function to export a final result."""
        # in TTT stage ranks are based on individual elapsed time,
        # and will be incomplete until all riders are confirmed
        self.recalculate()
        mcat = self.ridercat(cat)
        rcount = 0
        cnt = 0
        aux = []
        for r in self.riders:
            cnt += 1
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
                # this rider is 'in' the cat
                bib = r[COL_BIB]
                bonus = None
                ft = None
                crank = ''
                if r[COL_INRACE]:
                    # start offset is already accounted for in recalc
                    ft = self.vbunch(r[COL_CBUNCH], r[COL_MBUNCH])
                    if r[COL_PLACE] and r[COL_PLACE].isdigit():
                        crank = r[COL_PLACE]
                else:
                    crank = r[COL_COMMENT]
                if (bib in self.bonuses or r[COL_BONUS] is not None):
                    bonus = tod.ZERO
                    if bib in self.bonuses:
                        bonus += self.bonuses[bib]
                    if r[COL_BONUS] is not None:
                        bonus += r[COL_BONUS]
                penalty = None
                if r[COL_PENALTY] is not None:
                    penalty = r[COL_PENALTY]
                indRank = strops.dnfcode_key(crank)
                ftRank = tod.MAX
                if r[COL_INRACE] and ft is not None:
                    ftRank = ft
                yrec = [crank, bib, ft, bonus, penalty]
                aux.append((ftRank, indRank, cnt, yrec))
        aux.sort()
        lrank = None
        crank = None
        cnt = 0
        for r in aux:
            cnt += 1
            yrec = r[3]
            if yrec[0].isdigit():
                if yrec[2] is not None:
                    if r[1] != lrank:
                        crank = cnt
                        lrank = r[1]
                    yrec[0] = crank
                else:
                    yrec[0] = None
            yield yrec

    def addrider(self, bib='', series=None):
        """Add specified rider to race model."""
        if series is not None and series != self.series:
            _log.debug('Ignoring non-series rider: %r',
                       strops.bibser2bibstr(bib, series))
            return None
        if bib == '' or self.getrider(bib) is None:
            nr = [
                bib, '', '', '', '', True, '', 0, 0, None, None, None,
                tod.ZERO, None, None, [], ''
            ]
            dbr = self.meet.rdb.get_rider(bib, self.series)
            if dbr is not None:
                self.updaterider(nr, dbr)
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
        lr[COL_TEAM] = r['org'].upper()

    def updateteam(self, team=None):
        """Handle a change in team data"""
        # assume the worst and recalc all riders
        self.team_start_times()

    def resettimer(self):
        """Reset race timer."""
        _log.info('Reset event to idle')
        self.meet.alttimer.dearm(1)
        self.set_start()
        self.clear_results()
        self.timerstat = 'idle'
        self.meet.cmd_announce('timerstat', 'idle')
        self.meet.stat_but.update('idle', 'Idle')
        self.meet.stat_but.set_sensitive(True)
        self.elaplbl.set_text('')
        self.live_announce = True

    def armfinish(self):
        """Process an armfinish request."""
        if self.timerstat in ['running', 'finished']:
            self.armlap()
            self.timerstat = 'armfinish'
            self.meet.cmd_announce('timerstat', 'armfinish')
            self.meet.stat_but.update('error', 'Arm Finish')
            self.meet.stat_but.set_sensitive(True)
            self.meet.alttimer.armlock(True)
            self.meet.alttimer.arm(1)
        elif self.timerstat == 'armfinish':
            self.meet.alttimer.dearm(1)
            self.timerstat = 'running'
            self.meet.cmd_announce('timerstat', 'running')
            self.meet.stat_but.update('ok', 'Running')

    def armlap(self):
        ## announce text handling...
        self.scratch_map = {}
        self.scratch_ord = []
        titlestr = self.title_namestr.get_text()
        if self.live_announce:
            self.meet.cmd_announce('clear', 'all')
            self.meet.cmd_announce('title', titlestr)
        self.meet.cmd_announce('finstr', self.meet.get_short_name())
        self.running_team = None
        self.meet.cmd_announce(command='teamtime', msg='')
        self.elaplbl.set_text('')

    def starttrig(self, e):
        """Process a 'start' trigger signal."""
        if self.timerstat == 'armstart':
            _log.info('Start trigger: %s@%s/%s', e.chan, e.rawtime(), e.source)
            self.set_start(e)
        else:
            _log.info('Trigger: %s@%s/%s', e.chan, e.rawtime(), e.source)
        return False

    def alttimertrig(self, e):
        """Record timer message from alttimer."""
        # note: these impulses are sourced from alttimer device and keyboard
        #       transponder triggers are collected separately in timertrig()
        _log.debug('Alt timer: %s@%s/%s', e.chan, e.rawtime(), e.source)
        channo = strops.chan2id(e.chan)
        if channo == 1:
            # this is a finish impulse, treat as an n'th wheel indicator
            if self.timerstat == 'armfinish':
                _log.info('Team finish: %s', e.rawtime())
        else:
            # send through to catch-all trigger handler
            self.starttrig(e)

    def riderlap(self, bib, lr, rcat, e):
        """Process an accepted rider lap passing"""
        # check if lap mode is target-based
        lapfinish = False
        targetlap = None
        if self.targetlaps:
            # category laps override event laps
            if rcat in self.catlaps and self.catlaps[rcat]:
                targetlap = self.catlaps[rcat]
            else:
                targetlap = self.totlaps
            if targetlap and lr[COL_LAPS] >= targetlap - 1:
                lapfinish = True  # arm just this rider

        # finishing rider path
        if self.timerstat == 'armfinish' or lapfinish:
            if lr[COL_RFTIME] is None:
                if lr[COL_COMMENT] != 'wd':
                    if lr[COL_PLACE] == '':
                        lr[COL_RFTIME] = e
                        self._dorecalc = True
                    else:
                        _log.error('Placed rider seen at finish: %s:%s@%s/%s',
                                   bib, e.chan, e.rawtime(2), e.source)
                    if lr[COL_INRACE]:
                        lr[COL_LAPS] += 1
                        self.announce_rider('', bib, lr[COL_NAMESTR],
                                            lr[COL_CAT], e)
            else:
                _log.info('Duplicate finish rider %s:%s@%s/%s', bib, e.chan,
                          e.rawtime(2), e.source)
        # end finishing rider path

        # lapping rider path
        elif self.timerstat in ['running']:  # not finished, not armed
            self._dorecalc = True
            if lr[COL_INRACE] and (lr[COL_PLACE] or lr[COL_CBUNCH] is None):
                # rider in the race, not yet finished: increment own lap count
                lr[COL_LAPS] += 1

                # announce all rider passings
                self.announce_rider('', bib, lr[COL_NAMESTR], lr[COL_CAT], e)
        return False

    def finsprint(self, places):
        """Display a final sprint 'provisional' result."""
        # TODO: output team result from here
        pass

    def reannounce_times(self):
        """Re-send the current timing values."""
        # TODO
        self.meet.cmd_announce('timerstat', self.timerstat)
        return False

    def timeout(self):
        """Update elapsed time and recalculate if required."""
        if not self.winopen:
            return False
        if self._dorecalc:
            self.recalculate()
            if self.autoexport:
                GLib.idle_add(self.meet.menu_data_results_cb, None)
        if self.running_team is not None:
            # bounce a running time onto the panel
            self.bounceruntime(self.running_team, '')
        return True

    def set_start(self, start=''):
        """Set the start time."""
        if isinstance(start, tod.tod):
            self.start = start
        else:
            self.start = tod.mktod(start)
        if self.start is not None:
            self.set_running()

    def set_finished(self):
        """Update event status to finished."""
        self.timerstat = 'finished'
        self.meet.cmd_announce('timerstat', 'finished')
        self.meet.cmd_announce('laplbl', None)
        self.meet.stat_but.update('idle', 'Finished')
        self.meet.stat_but.set_sensitive(False)

    def info_time_edit_clicked_cb(self, button, data=None):
        """Run an edit times dialog to update event time."""
        st = ''
        if self.start is not None:
            st = self.start.rawtime(2)
        ft = '[n/a]'
        ret = uiutil.edit_times_dlg(self.meet.window,
                                    stxt=st,
                                    ftxt=ft,
                                    finish=False)
        if ret[0] == 1:
            wasrunning = self.timerstat in ['running', 'armfinish']
            self.set_start(ret[1])
            if wasrunning:
                # flag a recalculate
                self._dorecalc = True
            _log.info('Adjusted event times')

    def editcat_cb(self, cell, path, new_text, col):
        """Edit the cat field if valid."""
        new_text = ' '.join(new_text.strip().upper().split())
        r = self.meet.rdb.get_rider(self.riders[path][COL_BIB], self.series)
        if r is not None:
            # note: this will generate a rider change callback
            r['cat'] = new_text
        self.riders[path][col] = new_text

    def showstart_cb(self, col, cr, model, iter, data=None):
        """Draw start time offset in rider view."""
        st = model.get_value(iter, COL_STOFT)
        otxt = ''
        if st is not None:
            otxt = st.rawtime(0)
        cr.set_property('text', otxt)

    def editbunch_cb(self, cell, path, new_text, col=None):
        """Edit bunch time on rider view."""
        new_text = new_text.strip()
        dorecalc = False
        if new_text == '':  # user request to clear RFTIME?
            self.riders[path][COL_RFTIME] = None
            self.riders[path][COL_MBUNCH] = None
            self.riders[path][COL_CBUNCH] = None
            dorecalc = True
        else:
            # get 'current bunch time'
            omb = self.vbunch(self.riders[path][COL_CBUNCH],
                              self.riders[path][COL_MBUNCH])

            # assign new bunch time
            # note: ttt does not use + times or cascade
            nmb = tod.mktod(new_text)
            if self.riders[path][COL_MBUNCH] != nmb:
                self.riders[path][COL_MBUNCH] = nmb
                dorecalc = True
        if dorecalc:
            self.recalculate()

    def bounceteam(self, team, cat, time):
        """Bounce a teamname and time onto the panel"""
        team = team.upper()
        tname = ''
        tcat = self.ridercat(cat)
        # lookup team name in rdb
        dbr = self.meet.rdb.get_rider(team, 'team')
        if dbr is not None:
            tname = dbr['first']
        tstr = time.rawtime(1) + ' '  # hunges blanked
        self.meet.cmd_announce(command='teamtime',
                               msg='\x1f'.join(
                                   (tcat, team.upper(), tname, '', tstr)))
        self.elaplbl.set_text('%s: %s' % (team.upper(), tstr))
        return False

    def bounceruntime(self, team, cat):
        """Bounce a teamname and running time onto the panel"""
        team = team.upper()
        tname = ''
        tcat = self.ridercat(cat)
        tstr = ''
        tstart = None
        # lookup team name in rdb
        dbr = self.meet.rdb.get_rider(team, 'team')
        if dbr is not None:
            tname = dbr['first']
            tstart = tod.mktod(dbr['refid'])
        if team in self.teamtimes:
            tstr = self.teamtimes[team].rawtime(1) + ' '
        else:
            if tstart is not None:
                tstr = (tod.now() - tstart).rawtime(0)
        self.meet.cmd_announce(command='teamtime',
                               msg='\x1f'.join(
                                   (tcat, team.upper(), tname, '', tstr)))
        self.elaplbl.set_text('%s: %s' % (team.upper(), tstr))
        return False

    def _recalc(self):
        """Internal 'protected' recalculate function."""
        # if readonly and calc set - skip recalc
        if self.readonly and self.calcset:
            _log.debug('Cached Recalculate')
            return False

        if self.start is None:
            return

        _log.debug('Recalculate model')
        # clear off old places and bonuses
        self.resetplaces()
        self.teamtimes = {}

        # assign places
        self.assign_finish()
        for c in self.contests:
            self.assign_places(c)

        # arrange all riders in team groups by start time and team code
        aux = []
        idx = 0
        for r in self.riders:
            stime = r[COL_STOFT]
            tlabel = r[COL_TEAM]
            inrace = r[COL_INRACE]
            rbib = r[COL_BIB]
            rplace = r[COL_PLACE]
            rtime = tod.MAX
            rlaps = r[COL_LAPS]
            if r[COL_RFTIME] is not None:
                rtime = r[COL_RFTIME]
            if not inrace:
                rtime = tod.MAX
                rplace = r[COL_COMMENT]
            aux.append((stime, tlabel, not inrace, strops.dnfcode_key(rplace),
                        -rlaps, rtime, idx, rbib))
            idx += 1
        if len(aux) > 1:
            aux.sort()
            self.riders.reorder([a[6] for a in aux])

        # re-build cached team map
        cteam = None
        self.teammap = {}
        for r in self.riders:
            nteam = r[COL_TEAM]
            if nteam != cteam:
                # only rebuild cat and nth on first load
                if nteam not in self.teamcats:
                    ncat = self.ridercat(r[COL_CAT])
                    nth = self.defaultnth  # overridden by cat
                    if ncat in self.nthwheel:
                        try:
                            nth = int(self.nthwheel[ncat])
                            _log.debug('%s: %r nth wheel = %r', ncat, nteam,
                                       nth)
                        except Exception as e:
                            _log.warn('%s: %r invalid nth wheel %r set to: %r',
                                      ncat, nteam, self.nthwheel[ncat], nth)
                    self.teamnth[nteam] = nth
                    self.teamcats[nteam] = ncat
                self.teammap[nteam] = []
                cteam = nteam
            # cteam will be valid at this point
            if r[COL_RFTIME] is not None:  # will already be sorted!
                self.teammap[cteam].append(r)
                if r[COL_RFTIME] > self.maxfinish:
                    self.maxfinish = r[COL_RFTIME]

        # scan each team for times
        for t in self.teammap:
            # unless team has n finishers, there is no time
            tlist = self.teammap[t]
            nth_wheel = self.teamnth[t]
            if len(tlist) >= nth_wheel:
                ct = (tlist[nth_wheel - 1][COL_RFTIME] - self.start -
                      tlist[nth_wheel - 1][COL_STOFT])
                thetime = ct.truncate(1)
                self.teamtimes[t] = thetime  # save to times map
                if (t not in self.announced_teams and
                    (self.announce_team is None or self.announce_team == t)):
                    # bounce this time onto the panel? HACK
                    self.announced_teams.add(t)
                    self.running_team = None  # cancel a running time
                    self.bounceteam(t, self.teamcats[t], thetime)
                    self.announce_team = None
                for r in tlist[0:nth_wheel]:
                    r[COL_CBUNCH] = thetime
                for r in tlist[nth_wheel:]:
                    et = r[COL_RFTIME] - self.start - r[COL_STOFT]
                    if self.owntime and (et > ct and (et - ct) > GAPTHRESH):
                        # TIME GAP!
                        thetime = et.truncate(1)
                    r[COL_CBUNCH] = thetime
                    ct = et

        # leave mode sorted by arrival order
        self.reorder_arrivals()  # re-order view by arrivals at finish

        # if final places in view, update text entry
        curact = self.meet.action_model.get_value(
            self.meet.action_combo.get_active_iter(), 0)
        if curact == 'fin':
            self.meet.action_entry.set_text(self.places)
        #_log.debug(u'Event status: %r', self.racestat)
        self.calcset = True
        return False  # allow idle add

    def __init__(self, meet, event, ui=True):
        self.meet = meet
        self.event = event
        self.evno = event['evid']
        self.series = event['seri']
        self.configfile = meet.event_configfile(self.evno)
        self.readonly = not ui
        rstr = ''
        if self.readonly:
            rstr = 'readonly '
        _log.debug('Init %r event %r', rstr, self.evno)

        self.recalclock = threading.Lock()
        self._dorecalc = False

        self.teamnames = {}
        self.teamtimes = {}
        self.teamnth = {}
        self.teamcats = {}
        self.teamuci = {}
        self.teammap = {}
        self.announced_teams = set()
        self.announce_team = None
        self.running_team = None  # show running time for team

        # event run time attributes
        self.autoexport = False
        self.autofinish = False
        self.showuciids = False
        self.relativestart = False
        self.showriders = True
        self.owntime = True  # dropped riders get own time
        self.start = None
        self.calcset = False
        self.maxfinish = tod.ZERO
        self.minlap = None
        self.winopen = True
        self.timerstat = 'idle'
        self.places = ''
        self.comment = []
        self.ridermark = None
        self.cats = []
        self.passingsource = []  # list of decoders we accept passings from
        self.targetlaps = False  # true if finish is det by target
        self.catplaces = {}
        self.catlaps = {}  # cache of cat lap counts
        self.defaultnth = NTH_WHEEL
        self.nthwheel = {}
        self.autocats = False
        self.autostartlist = None
        self.bonuses = {}
        self.points = {}
        self.pointscb = {}
        self.totlaps = None
        self.catonlap = {}  # onlap per category
        self.clubmode = False
        self.allowspares = False
        self.gapthresh = GAPTHRESH  # time gap to set new time

        # intermediates
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

        self.riders = Gtk.ListStore(
            str,  # gobject.TYPE_STRING,  # BIB = 0
            str,  # gobject.TYPE_STRING,  # NAMESTR = 1
            str,  # gobject.TYPE_STRING,  # SHORTNAME = 2
            str,  # gobject.TYPE_STRING,  # CAT = 3
            str,  # gobject.TYPE_STRING,  # COMMENT = 4
            bool,  # gobject.TYPE_BOOLEAN,  # INRACE = 5
            str,  # gobject.TYPE_STRING,  # PLACE = 5
            int,  # gobject.TYPE_INT,  # LAP COUNT = 7
            int,  # gobject.TYPE_INT,  # SEED = 8
            object,  # gobject.TYPE_PYOBJECT,  # RFTIME = 9
            object,  # gobject.TYPE_PYOBJECT,  # CBUNCH = 10
            object,  # gobject.TYPE_PYOBJECT,  # MBUNCH = 11
            object,  # gobject.TYPE_PYOBJECT,  # STOFT = 12
            object,  # gobject.TYPE_PYOBJECT,  # BONUS = 13
            object,  # gobject.TYPE_PYOBJECT,  # PENALTY = 14
            object,  # gobject.TYPE_PYOBJECT,  # RFSEEN = 15
            str,  # gobject.TYPE_STRING)  # TEAM = 16
        )

        b = uiutil.builder('rms.ui')
        self.frame = b.get_object('race_vbox')
        self.frame.connect('destroy', self.shutdown)

        # meta info pane
        self.shortname = None
        self.title_namestr = b.get_object('title_namestr')
        self.set_titlestr()
        self.elaplbl = b.get_object('time_lbl')
        self.lapentry = b.get_object('lapentry')
        b.get_object('lapsepslash').set_text(' Total Laps:')
        self.lapentry.hide()
        self.totlapentry = b.get_object('totlapentry')

        # Result pane
        t = Gtk.TreeView(self.riders)
        self.view = t
        t.set_reorderable(True)
        t.set_rules_hint(True)

        self.context_menu = None
        if ui:
            uiutil.mkviewcoltxt(t, 'No.', COL_BIB, calign=1.0)
            uiutil.mkviewcoltxt(t,
                                'Rider',
                                COL_NAMESTR,
                                expand=True,
                                maxwidth=500)
            uiutil.mkviewcoltxt(t, 'Cat', COL_CAT)
            uiutil.mkviewcoltxt(t, 'Com', COL_COMMENT, cb=self.editcol_cb)
            uiutil.mkviewcoltxt(t,
                                'Lap',
                                COL_LAPS,
                                width=40,
                                cb=self.editlap_cb)
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
            uiutil.mkviewcoltxt(t, 'Arvl', COL_PLACE, calign=0.5, width=50)
            t.show()
            b.get_object('race_result_win').add(t)
            b.connect_signals(self)

            b = uiutil.builder('rms_context.ui')
            self.context_menu = b.get_object('rms_context')
            self.view.connect('button_press_event', self.treeview_button_press)
            b.connect_signals(self)
            self.meet.timercb = self.timertrig
            self.meet.alttimercb = self.alttimertrig
