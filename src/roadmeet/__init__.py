# SPDX-License-Identifier: MIT
"""Timing and data handling application wrapper for road events."""

import sys
import gi
import logging
import metarace
import csv
import os

gi.require_version("GLib", "2.0")
from gi.repository import GLib

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

from metarace import jsonconfig
from metarace import tod
from metarace import riderdb
from metarace import eventdb
from metarace.telegraph import telegraph
from metarace import export
from metarace.decoder import decoder
from metarace.decoder.rru import rru
from metarace.decoder.rrs import rrs
from metarace.decoder.thbc import thbc
from metarace.timy import timy
from metarace import strops
from metarace import report

from . import uiutil
from roadmeet.rms import rms
from roadmeet.irtt import irtt
from roadmeet.trtt import trtt

LOGFILE = 'event.log'
LOGFILE_LEVEL = logging.DEBUG
CONFIGFILE = 'config.json'
ROADMEET_ID = 'roadmeet_3.0'  # configuration versioning
EXPORTPATH = 'export'
_log = logging.getLogger('metarace.roadmeet')
_log.setLevel(logging.DEBUG)
ROADRACE_TYPES = {
    'road': 'Road Race',
    'circuit': 'Circuit',
    'criterium': 'Criterium',
    'handicap': 'Handicap',
    'cross': 'Cyclocross',
    'irtt': 'Road Time Trial',
    'trtt': 'Team Road Time Trial',
}
_DEFAULT_HANDLER = 'null'
_HANDLERS = {
    'null': decoder,
    'thbc': thbc,
    'rrs': rrs,
    'rru': rru,
}


def mkdevice(portstr='', curdev=None):
    """Return a decoder handle for the provided port specification."""
    # Note: If possible, returns the current device
    ret = curdev
    devtype = _DEFAULT_HANDLER
    if metarace.sysconf.has_option('decoder', 'default'):
        devtype = metarace.sysconf.get('decoder', 'default')
        _log.debug('Default type set to %r from sysconf', devtype)
    (a, b, c) = portstr.partition(':')
    if b:
        a = a.lower()
        if a in _HANDLERS:
            devtype = a
        a = c  # shift port into a
    devport = a
    if curdev is None:
        curdev = _HANDLERS[devtype]()
        curdev.setport(devport)
    elif type(curdev) is _HANDLERS[devtype]:
        _log.debug('Requested decoder is %s', curdev.__class__.__name__)
        curdev.setport(devport)
    else:
        _log.debug('Changing decoder type from %s to %s',
                   curdev.__class__.__name__, devtype)
        curdev.setcb(None)
        wasalive = curdev.running()
        if wasalive:
            curdev.exit('Change decoder type')
        curdev = None
        curdev = _HANDLERS[devtype]()
        curdev.setport(devport)
        _log.debug('Starting %s decoder', curdev.__class__.__name__)
        if wasalive:
            curdev.start()
    return curdev


class roadmeet:
    """Road meet application class."""

    ## Meet Menu Callbacks
    def menu_meet_save_cb(self, menuitem, data=None):
        """Save current all meet data to config."""
        self.saveconfig()

    def get_short_name(self):
        """Return the <= 16 char shortname."""
        return self.shortname

    def cat_but_auto_clicked(self, but, entry, data=None):
        """Lookup cats and write them into the supplied entry."""
        entry.set_text(' '.join(self.rdb.listcats()))

    def menu_race_properties_activate_cb(self, menuitem, data=None):
        """Edit race specific properties."""
        if self.curevent is not None:
            _log.debug('Editing race properties')
            self.curevent.edit_event_properties(self.window)

    def menu_meet_properties_cb(self, menuitem, data=None):
        """Edit meet properties."""
        b = uiutil.builder('roadmeet_props.ui')
        dlg = b.get_object('properties')
        dlg.set_transient_for(self.window)

        # setup the type entry
        tcombo = b.get_object('type_combo')
        tmodel = b.get_object('type_model')
        tlbl = self.etype
        dotype = False
        # correct empty type
        if self.etype == '':
            self.etype = 'road'
        if self.etype in ROADRACE_TYPES:
            tlbl = ROADRACE_TYPES[self.etype]
            dotype = True
            cnt = 0
            for t in [
                    'road', 'circuit', 'handicap', 'criterium', 'cross',
                    'irtt', 'trtt'
            ]:
                tmodel.append([t, ROADRACE_TYPES[t]])
                if t == self.etype:
                    tcombo.set_active(cnt)
                cnt += 1
            tcombo.set_sensitive(True)
        else:
            _log.warning('Unknown event type %r', self.etype)
            tmodel.append([self.etype, tlbl])
            tcombo.set_active(0)
            tcombo.set_sensitive(False)

        # fetch event result categories
        ocats = []
        cat_ent = b.get_object('cat_entry')
        if self.curevent is not None:
            ocats = self.curevent.get_catlist()
            cat_ent.set_text(' '.join(ocats))
            cba = b.get_object('cat_but_auto')
            cba.connect('clicked', self.cat_but_auto_clicked, cat_ent)

        # fill text entries
        t_ent = b.get_object('title_entry')
        t_ent.set_text(self.title_str)
        st_ent = b.get_object('subtitle_entry')
        st_ent.set_text(self.subtitle_str)
        doc_ent = b.get_object('document_entry')
        doc_ent.set_text(self.document_str)
        d_ent = b.get_object('date_entry')
        d_ent.set_text(self.date_str)
        o_ent = b.get_object('organiser_entry')
        o_ent.set_text(self.organiser_str)
        c_ent = b.get_object('commissaire_entry')
        c_ent.set_text(self.commissaire_str)
        di_ent = b.get_object('distance_entry')
        if self.distance is not None:
            di_ent.set_text(str(self.distance))
        dis_ent = b.get_object('diststr_entry')
        if self.diststr is not None:
            dis_ent.set_text(self.diststr)
        ate = b.get_object('announce_topic_entry')
        if self.anntopic is not None:
            ate.set_text(self.anntopic)
        tte = b.get_object('timing_topic_entry')
        if self.timertopic is not None:
            tte.set_text(self.timertopic)
        ren = b.get_object('remote_enable_check')
        ren.set_active(self.remote_enable)
        mte = b.get_object('timing_main_entry')
        mte.set_text(self.timer_port)
        alte = b.get_object('timing_alt_entry')
        alte.set_text(self.alttimer_port)
        response = dlg.run()
        if response == 1:  # id 1 set in glade for "Apply"
            _log.debug('Updating meet properties')
            self.title_str = t_ent.get_text()
            self.subtitle_str = st_ent.get_text()
            self.document_str = doc_ent.get_text()
            self.date_str = d_ent.get_text()
            self.organiser_str = o_ent.get_text()
            self.commissaire_str = c_ent.get_text()
            self.distance = strops.confopt_float(di_ent.get_text())
            self.diststr = dis_ent.get_text()

            # 'announce' topic
            ntopic = ate.get_text()
            if ntopic != self.anntopic:
                if self.anntopic is not None:
                    self.announce.unsubscribe('/'.join(
                        (self.anntopic, 'control', '#')))
                self.anntopic = None
                if ntopic:
                    self.anntopic = ntopic
                    self.announce.subscribe('/'.join(
                        (self.anntopic, 'control', '#')))
            # remote timer topic
            ntopic = tte.get_text()
            if ntopic != self.timertopic:
                if self.timertopic is not None:
                    self.announce.unsubscribe(self.timertopic)
                self.timertopic = None
                if ntopic:
                    self.timertopic = ntopic

            # update remote subscription
            self.remote_enable = ren.get_active()
            self.remote_reset()

            # reset timer
            self.set_timer(mte.get_text())

            nport = alte.get_text()
            if nport != self.alttimer_port:
                self.alttimer_port = nport
                self.alttimer.setport(nport)

            reload = False
            if self.curevent is not None:
                ncats = cat_ent.get_text().split()
                if ncats != ocats:
                    _log.debug('Result cats changed %r -> %r', ocats, ncats)
                    self.curevent.loadcats(ncats)
                    reload = True
            nt = tmodel.get_value(tcombo.get_active_iter(), 0)
            if dotype:
                # check for type change
                if nt != self.etype:
                    _log.info('Event type changed from %r to %r', self.etype,
                              nt)
                    if nt == 'crit':
                        self.curevent.downtimes(False)
                    else:
                        self.curevent.downtimes(True)
                    reload = True
            if reload:
                event = self.edb.getfirst()
                event['type'] = nt
                self.etype = nt
                self.menu_race_run_activate_cb(None, None)

            self.set_title()
            _log.debug('Properties updated')
        else:
            _log.debug('Edit properties cancelled')
        dlg.destroy()

    def print_report(self, sections=[], provisional=False):
        """Print the pre-formatted sections in a standard report."""
        rep = report.report()
        rep.provisional = provisional
        rep.strings['title'] = self.title_str
        rep.strings['host'] = self.host_str
        rep.strings['subtitle'] = self.subtitle_str
        rep.strings['docstr'] = self.document_str
        rep.strings['datestr'] = strops.promptstr('Date:', self.date_str)
        rep.strings['commstr'] = strops.promptstr('Chief Commissaire:',
                                                  self.commissaire_str)
        rep.strings['orgstr'] = strops.promptstr('Organiser:',
                                                 self.organiser_str)
        if self.distance:
            rep.strings['diststr'] = strops.promptstr(
                'Distance:',
                str(self.distance) + '\u2006km')
        else:
            rep.strings['diststr'] = self.diststr

        if self.eventcode:
            rep.eventid = self.eventcode
        if self.prevlink:
            rep.prevlink = self.prevlink
        if self.nextlink:
            rep.nextlink = self.nextlink
        if self.indexlink:
            rep.indexlink = self.indexlink
        if self.shortname:
            rep.shortname = self.shortname
        for sec in sections:
            rep.add_section(sec)

        print_op = Gtk.PrintOperation.new()
        print_op.set_allow_async(True)
        print_op.set_print_settings(self.printprefs)
        print_op.set_default_page_setup(self.pageset)
        print_op.connect('begin_print', self.begin_print, rep)
        print_op.connect('draw_page', self.draw_print_page, rep)
        res = print_op.run(Gtk.PrintOperationAction.PREVIEW, None)
        if res == Gtk.PrintOperationResult.APPLY:
            self.printprefs = print_op.get_print_settings()
            _log.debug('Updated print preferences')
        elif res == Gtk.PrintOperationResult.IN_PROGRESS:
            _log.debug('Print operation in progress')
        self.docindex += 1

        # For convenience, also save copies to pdf and xls
        ofile = 'output.pdf'
        with metarace.savefile(ofile, mode='b') as f:
            rep.output_pdf(f)
        ofile = 'output.xls'
        with metarace.savefile(ofile, mode='b') as f:
            rep.output_xls(f)
        return False

    def begin_print(self, operation, context, rep):
        """Set print pages and units."""
        rep.start_gtkprint(context.get_cairo_context())
        operation.set_use_full_page(True)
        operation.set_n_pages(rep.get_pages())
        operation.set_unit(Gtk.Unit.POINTS)

    def draw_print_page(self, operation, context, page_nr, rep):
        """Draw to the nominated page."""
        rep.set_context(context.get_cairo_context())
        rep.draw_page(page_nr)

    def menu_meet_quit_cb(self, menuitem, data=None):
        """Quit the application."""
        self.window.close()

    ## Race Menu Callbacks
    def menu_race_run_activate_cb(self, menuitem=None, data=None):
        """Open the event handler."""
        eh = self.edb.getfirst()
        if eh is not None:
            self.open_event(eh)
            self.set_title()

    def menu_race_close_activate_cb(self, menuitem, data=None):
        """Close callback - disabled in roadrace."""
        self.close_event()

    def menu_race_abort_activate_cb(self, menuitem, data=None):
        """Close the currently open event without saving."""
        if self.curevent is not None:
            self.curevent.readonly = True
        self.close_event()

    def menu_race_armstart_activate_cb(self, menuitem, data=None):
        """Default armstart handler."""
        _log.info('Arm Start')
        try:
            self.curevent.armstart()
        except Exception as e:
            _log.error('Arm start %s: %s', e.__class__.__name__, e)

    def menu_race_armlap_activate_cb(self, menuitem, data=None):
        """Default armlap handler."""
        _log.debug('Arm Lap')
        try:
            self.curevent.armlap()
        except Exception as e:
            _log.error('Arm lap %s: %s', e.__class__.__name__, e)

    def menu_race_armfin_activate_cb(self, menuitem, data=None):
        """Default armfin handler."""
        _log.info('Arm Finish')
        try:
            self.curevent.armfinish()
        except Exception as e:
            _log.error('Arm finish %s: %s', e.__class__.__name__, e)

    def menu_race_finished_activate_cb(self, menuitem, data=None):
        """Default finished handler."""
        _log.info('Finished')
        try:
            self.curevent.set_finished()
        except Exception as e:
            _log.error('Set finished %s: %s', e.__class__.__name__, e)

    def open_event(self, eventhdl=None):
        """Open provided event handle."""
        if eventhdl is not None:
            self.close_event()
            if self.etype not in ROADRACE_TYPES:
                _log.warning('Unknown event type %r', self.etype)
            if self.etype == 'irtt':
                self.curevent = irtt(self, eventhdl, True)
            elif self.etype == 'trtt':
                self.curevent = trtt(self, eventhdl, True)
            else:
                self.curevent = rms(self, eventhdl, True)

            self.curevent.loadconfig()
            self.race_box.add(self.curevent.frame)

            # re-populate the rider command model.
            cmdo = self.curevent.get_ridercmdorder()
            cmds = self.curevent.get_ridercmds()
            if cmds is not None:
                self.action_model.clear()
                for cmd in cmdo:
                    self.action_model.append([cmd, cmds[cmd]])
                self.action_combo.set_active(0)

            self.menu_race_close.set_sensitive(True)
            self.menu_race_abort.set_sensitive(True)
            starters = eventhdl['star']
            if starters is not None and starters != '':
                self.curevent.race_ctrl('add', starters)
                eventhdl['star'] = ''  # and clear
            self.curevent.show()

    def close_event(self):
        """Close the currently opened race."""
        if self.curevent is not None:
            if self.curevent.frame in self.race_box.get_children():
                self.race_box.remove(self.curevent.frame)
            self.curevent.destroy()
            self.menu_race_close.set_sensitive(False)
            self.menu_race_abort.set_sensitive(False)
            self.curevent = None
            self.stat_but.update('idle', 'Closed')
            self.stat_but.set_sensitive(False)

    ## Reports menu callbacks.
    def menu_reports_startlist_activate_cb(self, menuitem, data=None):
        """Generate a startlist."""
        if self.curevent is not None:
            sections = self.curevent.startlist_report()
            if sections:
                self.print_report(sections)
            else:
                _log.info('Startlist - Nothing to print')

    def menu_reports_callup_activate_cb(self, menuitem, data=None):
        """Generate a start line call-up."""
        if self.curevent is not None:
            sections = self.curevent.callup_report()
            if sections:
                self.print_report(sections)
            else:
                _log.info('Callup - Nothing to print')

    def menu_reports_signon_activate_cb(self, menuitem, data=None):
        """Generate a sign on sheet."""
        if self.curevent is not None:
            sections = self.curevent.signon_report()
            if sections:
                self.print_report(sections)
            else:
                _log.info('Sign-on - Nothing to print')

    def menu_reports_analysis_activate_cb(self, menuitem, data=None):
        """Generate the analysis report."""
        if self.curevent is not None:
            sections = self.curevent.analysis_report()
            if sections:
                self.print_report(sections)

    def menu_reports_camera_activate_cb(self, menuitem, data=None):
        """Generate the camera operator report."""
        if self.curevent is not None:
            sections = self.curevent.camera_report()
            if sections:
                self.print_report(sections)

    def race_results_points_activate_cb(self, menuitem, data=None):
        """Generate the points tally report."""
        if self.curevent is not None:
            sections = self.curevent.points_report()
            if sections:
                self.print_report(sections)

    def menu_reports_result_activate_cb(self, menuitem, data=None):
        """Generate the race result report."""
        if self.curevent is not None:
            sections = self.curevent.result_report()
            if sections:
                self.print_report(sections,
                                  self.curevent.timerstat != 'finished')

    def menu_data_replace_activate_cb(self, menuitem, data=None):
        """Replace rider db from disk."""
        sfile = uiutil.chooseCsvFile(title='Select rider file to load from',
                                     parent=self.window,
                                     path='.')
        if sfile is not None:
            try:
                self.rdb.clear(notify=False)
                count = self.rdb.load(sfile)
                _log.info('Loaded %d entries from %r', count, sfile)
                #self.menu_race_run_activate_cb()
            except Exception as e:
                _log.error('%s loading riders: %s', e.__class__.__name__, e)
        else:
            _log.debug('Import riders cancelled')

    def menu_data_clear_activate_cb(self, menuitem, data=None):
        """Clear rider db."""
        self.rdb.clear()
        _log.info('Cleared rider db')
        #self.menu_race_run_activate_cb()

    def menu_import_riders_activate_cb(self, menuitem, data=None):
        """Add riders to database."""
        sfile = uiutil.chooseCsvFile(title='Select rider file to import',
                                     parent=self.window,
                                     path='.')
        if sfile is not None:
            try:
                count = self.rdb.load(sfile, overwrite=True)
                _log.info('Imported %d rider entries from %r', count, sfile)
                #self.menu_race_run_activate_cb()
            except Exception as e:
                _log.error('%s importing riders: %s', e.__class__.__name__, e)
        else:
            _log.debug('Import riders cancelled')

    def menu_import_chipfile_activate_cb(self, menuitem, data=None):
        """Import a transponder chipfile."""
        sfile = uiutil.chooseCsvFile(title='Select chipfile to import',
                                     parent=self.window,
                                     path='.')
        if sfile is not None:
            try:
                count = self.rdb.load_chipfile(sfile)
                _log.info('Imported %d refids from chipfile %r', count, sfile)
            except Exception as e:
                _log.error('%s importing chipfile: %s', e.__class__.__name__,
                           e)
        else:
            _log.debug('Import chipfile cancelled')

    def menu_import_startlist_activate_cb(self, menuitem, data=None):
        """Import a startlist."""
        if self.curevent is None:
            _log.info('No event open for starters import')
            return
        count = 0
        sfile = uiutil.chooseCsvFile(title='Select startlist file to import',
                                     parent=self.window,
                                     path='.')
        if os.path.isfile(sfile):
            with open(sfile, encoding='utf-8', errors='replace') as f:
                cr = csv.reader(f)
                for r in cr:
                    if len(r) > 1 and r[1].isalnum() and r[1].lower() not in [
                            'no', 'no.'
                    ]:
                        bib = r[1].strip().lower()
                        series = ''
                        if len(r) > 2:
                            series = r[2].strip()
                        self.curevent.addrider(bib, series)
                        start = tod.mktod(r[0])
                        if start is not None:
                            self.curevent.starttime(start, bib, series)
                        count += 1
            _log.info('Imported %d starters from %r', count, sfile)
        else:
            _log.debug('Import startlist cancelled')

    def menu_export_riders_activate_cb(self, menuitem, data=None):
        """Export rider database."""
        sfile = uiutil.chooseCsvFile(title='Select file to export riders to',
                                     mode=Gtk.FileChooserAction.SAVE,
                                     parent=self.window,
                                     hintfile='riders_export.csv',
                                     path='.')
        if sfile is not None:
            try:
                self.rdb.save(sfile)
                _log.info('Export rider data to %r', sfile)
            except Exception as e:
                _log.error('%s exporting riders: %s', e.__class__.__name__, e)
        else:
            _log.debug('Export rider data cancelled')

    def menu_export_chipfile_activate_cb(self, menuitem, data=None):
        """Export transponder chipfile from rider model."""
        sfile = uiutil.chooseCsvFile(title='Select file to export refids to',
                                     mode=Gtk.FileChooserAction.SAVE,
                                     parent=self.window,
                                     hintfile='chipfile.csv',
                                     path='.')
        if sfile is not None:
            try:
                count = self.rdb.save_chipfile(sfile)
                _log.info('Exported %d refids to chipfile %r', count, sfile)
            except Exception as e:
                _log.error('%s exporting chipfile: %s', e.__class__.__name__,
                           e)
        else:
            _log.debug('Export chipfile cancelled')

    def menu_export_result_activate_cb(self, menuitem, data=None):
        """Export raw result to disk."""
        if self.curevent is None:
            _log.info('No event open')
            return

        rfilename = uiutil.chooseCsvFile(
            title='Select file to save results to.',
            mode=Gtk.FileChooserAction.SAVE,
            parent=self.window,
            hintfile='results.csv',
            path='.')
        if rfilename is not None:
            with metarace.savefile(rfilename) as f:
                cw = csv.writer(f)
                cw.writerow(['Rank', 'No.', 'Time', 'Bonus', 'Penalty'])
                for r in self.curevent.result_gen(''):
                    opr = ['', '', '', '', '']
                    for i in range(0, 2):
                        if r[i]:
                            opr[i] = str(r[i])
                    for i in range(2, 5):
                        if r[i]:
                            opr[i] = str(r[i].timeval)
                    cw.writerow(opr)
            _log.info('Export result to %r', rfilename)

    def menu_export_startlist_activate_cb(self, menuitem, data=None):
        """Extract startlist from current event."""
        if self.curevent is None:
            _log.info('No event open')
            return

        rfilename = uiutil.chooseCsvFile(
            title='Select file to save startlist to.',
            mode=Gtk.FileChooserAction.SAVE,
            parent=self.window,
            hintfile='startlist.csv',
            path='.')
        if rfilename is not None:
            with metarace.savefile(rfilename) as f:
                cw = csv.writer(f)
                cw.writerow(['Start', 'No.', 'Series', 'Name', 'Cat'])
                if self.etype == 'irtt':
                    for r in self.curevent.startlist_gen():
                        cw.writerow(r)
                else:
                    clist = self.curevent.get_catlist()
                    clist.append('')
                    for c in clist:
                        for r in self.curevent.startlist_gen(c):
                            cw.writerow(r)

            _log.info('Export startlist to %r', rfilename)
        else:
            _log.info('Export startlist cancelled')

    def export_result_maker(self):
        if self.mirrorfile:
            filebase = self.mirrorfile
        else:
            filebase = '.'
        if filebase in ['', '.']:
            filebase = ''
            _log.warn('Using default filenames for export')
        else:
            pass

        fnv = []
        if filebase:
            fnv.append(filebase)
        fnv.append('startlist')
        sfile = '_'.join(fnv)
        fnv[-1] = 'result'
        ffile = '_'.join(fnv)

        # Write out a startlist unless event finished
        if self.curevent.timerstat not in ['finished']:
            filename = sfile
            rep = report.report()
            rep.strings['title'] = self.title_str
            rep.strings['host'] = self.host_str
            rep.strings['subtitle'] = self.subtitle_str
            rep.strings['docstr'] = self.document_str
            rep.strings['datestr'] = strops.promptstr('Date:', self.date_str)
            rep.strings['commstr'] = strops.promptstr('Chief Commissaire:',
                                                      self.commissaire_str)
            rep.strings['orgstr'] = strops.promptstr('Organiser:',
                                                     self.organiser_str)
            if self.distance:
                rep.strings['diststr'] = strops.promptstr(
                    'Distance:',
                    str(self.distance) + '\u2006km')
            else:
                rep.strings['diststr'] = self.diststr
            if self.provisionalstart:
                rep.set_provisional(True)
            rep.indexlink = 'index'
            if self.eventcode:
                rep.eventid = self.eventcode
            if self.prevlink:
                rep.prevlink = '_'.join((self.prevlink, 'startlist'))
            if self.nextlink:
                rep.nextlink = '_'.join((self.nextlink, 'startlist'))
            if self.indexlink:
                rep.indexlink = self.indexlink
            if self.shortname:
                rep.shortname = self.shortname
            rep.resultlink = ffile
            for sec in self.curevent.startlist_report():
                rep.add_section(sec)

            lb = os.path.join(self.linkbase, filename)
            lt = ['pdf', 'xls']
            rep.canonical = '.'.join([lb, 'json'])
            ofile = os.path.join(self.exportpath, filename + '.pdf')
            with metarace.savefile(ofile, mode='b') as f:
                rep.output_pdf(f)
            ofile = os.path.join(self.exportpath, filename + '.xls')
            with metarace.savefile(ofile, mode='b') as f:
                rep.output_xls(f)
            ofile = os.path.join(self.exportpath, filename + '.json')
            with metarace.savefile(ofile) as f:
                rep.output_json(f)
            ofile = os.path.join(self.exportpath, filename + '.html')
            with metarace.savefile(ofile) as f:
                rep.output_html(f, linkbase=lb, linktypes=lt)

        # Then export a result
        rep = report.report()
        rep.strings['title'] = self.title_str
        rep.strings['host'] = self.host_str
        rep.strings['subtitle'] = self.subtitle_str
        rep.strings['docstr'] = self.document_str
        rep.strings['datestr'] = strops.promptstr('Date:', self.date_str)
        rep.strings['commstr'] = strops.promptstr('Chief Commissaire:',
                                                  self.commissaire_str)
        rep.strings['orgstr'] = strops.promptstr('Organiser:',
                                                 self.organiser_str)
        if self.distance:
            rep.strings['diststr'] = strops.promptstr(
                'Distance:',
                str(self.distance) + '\u2006km')
        else:
            rep.strings['diststr'] = self.diststr

        # Set provisional status	# TODO: other tests for prov flag?
        if self.curevent.timerstat != 'finished':
            rep.set_provisional(True)
        else:
            rep.reportstatus = 'final'  # TODO: write in other phases
        for sec in self.curevent.result_report():
            rep.add_section(sec)

        filename = ffile
        rep.indexlink = 'index'
        if self.eventcode:
            rep.eventid = self.eventcode
        if self.prevlink:
            rep.prevlink = '_'.join((self.prevlink, 'result'))
        if self.nextlink:
            rep.nextlink = '_'.join((self.nextlink, 'result'))
        if self.indexlink:
            rep.indexlink = self.indexlink
        if self.shortname:
            rep.shortname = self.shortname
        rep.startlink = sfile
        lb = os.path.join(self.linkbase, filename)
        lt = ['pdf', 'xls']
        rep.canonical = '.'.join([lb, 'json'])

        ofile = os.path.join(self.exportpath, filename + '.pdf')
        with metarace.savefile(ofile, mode='b') as f:
            rep.output_pdf(f)
        ofile = os.path.join(self.exportpath, filename + '.xls')
        with metarace.savefile(ofile, mode='b') as f:
            rep.output_xls(f)
        ofile = os.path.join(self.exportpath, filename + '.json')
        with metarace.savefile(ofile) as f:
            rep.output_json(f)
        ofile = os.path.join(self.exportpath, filename + '.html')
        with metarace.savefile(ofile) as f:
            rep.output_html(f, linkbase=lb, linktypes=lt)

    def menu_data_results_cb(self, menuitem, data=None):
        """Create live result report and export"""
        self.saveconfig()
        if self.curevent is None:
            return
        if self.lifexport:  # save current lif with export
            lifdat = self.curevent.lifexport()
            if len(lifdat) > 0:
                liffile = os.path.join(self.exportpath, 'lifexport.lif')
                with metarace.savefile(liffile) as f:
                    cw = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                    for r in lifdat:
                        cw.writerow(r)
        if self.resfiles:
            self.export_result_maker()
        GLib.idle_add(self.mirror_start)

    ## Directory utilities
    def event_configfile(self, evno):
        """Return a config filename for the given event no."""
        return 'event_{}.json'.format(str(evno))

    ## Timing menu callbacks
    def menu_timing_status_cb(self, menuitem, data=None):
        if self.timer_port:
            if self.timer.connected():
                _log.info('Request timer status')
                self.timer.status()
            else:
                _log.info('Decoder disconnected')
        else:
            _log.info('No decoder configured')
        # always call into alt timer
        self.alttimer.status()

    def menu_timing_start_activate_cb(self, menuitem, data=None):
        """Manually set race elapsed time via trigger."""
        if self.curevent is None:
            _log.info('No event open to set elapsed time on')
        else:
            self.curevent.elapsed_dlg()

    def entry_set_now(self, button, entry=None):
        """Enter the current time in the provided entry."""
        entry.set_text(tod.now().timestr())
        entry.activate()

    def menu_timing_recalc(self, entry, ste, fte, nte):
        """Update the net time entry for the supplied start and finish."""
        st = tod.mktod(ste.get_text())
        ft = tod.mktod(fte.get_text())
        if st is not None and ft is not None:
            ste.set_text(st.timestr())
            fte.set_text(ft.timestr())
            nte.set_text((ft - st).timestr())

    def menu_timing_clear_activate_cb(self, menuitem, data=None):
        """Start a new timing session in attached timers"""
        # Note: clear will perform reset, stop_session, clear,
        # sync, and start_session in whatever order is appropriate
        # for the decoder type
        self.timer.clear()
        self.alttimer.clrmem()

    def menu_timing_reconnect_activate_cb(self, menuitem, data=None):
        """Drop current timer connection and re-connect"""
        self.set_timer(self.timer_port, force=True)
        self.alttimer.setport(self.alttimer_port)
        self.alttimer.sane()
        if self.etype == 'irtt':
            self.alttimer.write('DTS05.00')
            self.alttimer.write('DTF00.01')
        else:
            # assume 1 second gaps at finish
            self.alttimer.write('DTF01.00')
        _log.info('Re-connect/re-start attached timers')

    def restart_decoder(self, data=None):
        """Request re-start of decoder."""
        self.timer.start_session()
        return None

    def menu_timing_configure_activate_cb(self, menuitem, data=None):
        """Attempt to re-configure the attached decoder from saved config."""
        if self.timer.__class__.__name__ == 'thbc':
            if not timer.connected():
                _log.info('Timer not connected, config not possible')
                return False
            if not uiutil.questiondlg(
                    self.window, 'Re-configure THBC Decoder Settings?',
                    'Note: Passings will not be captured while decoder is updating.'
            ):
                _log.debug('Config aborted')
                return False
            self.timer.stop_session()
            self.timer.sane()
            GLib.timeout_add_seconds(60, self.restart_decoder)
            self.timer.ipconfig()
        else:
            _log.info('Decoder config not available')
        return None

    ## Help menu callbacks
    def menu_help_about_cb(self, menuitem, data=None):
        """Display metarace about dialog."""
        uiutil.about_dlg(self.window)

    ## Race Control Elem callbacks
    def race_stat_but_clicked_cb(self, button, data=None):
        """Call through into event if open."""
        if self.curevent is not None:
            self.curevent.stat_but_clicked(button)

    def race_stat_entry_activate_cb(self, entry, data=None):
        """Pass the chosen action and bib list through to curevent."""
        action = self.action_model.get_value(
            self.action_combo.get_active_iter(), 0)
        if self.curevent is not None:
            if self.curevent.race_ctrl(action, self.action_entry.get_text()):
                self.action_entry.set_text('')

    ## Menu button callbacks
    def race_action_combo_changed_cb(self, combo, data=None):
        """Notify curevent of change in combo."""
        aiter = self.action_combo.get_active_iter()
        if self.curevent is not None and aiter is not None:
            action = self.action_model.get_value(aiter, 0)
            self.curevent.ctrl_change(action, self.action_entry)

    def menu_clock_clicked_cb(self, button, data=None):
        """Handle click on menubar clock."""
        _log.info('PC ToD: %s', tod.now().rawtime())

    ## 'Slow' Timer callback - this is the main ui event routine
    def timeout(self):
        """Update status buttons and time of day clock button."""
        if self.running:
            # call into race timeout handler
            if self.curevent is not None:
                self.curevent.timeout()

            # check for completion in the export thread
            if self.mirror is not None:
                if not self.mirror.is_alive():
                    self.mirror = None
                    _log.debug('Removing completed export thread.')

            # update the menu status button
            nt = tod.now().meridiem()
            if self.rfuact:
                self.rfustat.update('activity', nt)
            else:
                if self.timer_port:
                    if self.timer.connected():
                        self.rfustat.update('ok', nt)
                    else:
                        self.rfustat.update('error', nt)
                else:
                    self.rfustat.update('idle', nt)
            self.rfuact = False

            # attempt to heal a broken link
            if self.timer_port:
                if self.timer.connected():
                    self.rfufail = 0
                else:
                    self.rfufail += 1
                    if self.rfufail > 10:
                        self.rfufail = 0
                        eport = self.timer_port.split(':', 1)[-1]
                        self.timer.setport(eport)
            else:
                self.rfufail = 0
        else:
            return False
        return True

    ## Window methods
    def set_title(self, extra=''):
        """Update window title from meet properties."""
        tv = []
        if self.etype in ROADRACE_TYPES:
            tv.append(ROADRACE_TYPES[self.etype] + ':')

        title = self.title_str.strip()
        if title:
            tv.append(title)
        subtitle = self.subtitle_str.strip()
        if subtitle:
            tv.append(subtitle)
        self.window.set_title(' '.join(tv))
        if self.curevent is not None:
            self.curevent.set_titlestr(subtitle)

    def meet_destroy_cb(self, window, msg=''):
        """Handle destroy signal and exit application."""
        rootlogger = logging.getLogger()
        rootlogger.removeHandler(self.sh)
        rootlogger.removeHandler(self.lh)
        #self.window.hide()
        GLib.idle_add(self.meet_destroy_handler)

    def meet_destroy_handler(self):
        if self.curevent is not None:
            self.close_event()
        if self.started:
            self.saveconfig()
            self.shutdown()  # threads are joined in shutdown
        rootlogger = logging.getLogger()
        if self.loghandler is not None:
            rootlogger.removeHandler(self.loghandler)
        self.running = False
        Gtk.main_quit()
        return False

    def key_event(self, widget, event):
        """Collect key events on main window and send to race."""
        if event.type == Gdk.EventType.KEY_PRESS:
            key = Gdk.keyval_name(event.keyval) or 'None'
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                key = key.lower()
                t = tod.now(chan='MAN', refid=str(key))
                if key in ['0', '1']:
                    # trigger
                    t.refid = ''
                    t.chan = strops.id2chan(strops.chan2id(key))
                    self._alttimercb(t)
                    return True
                elif key in ['2', '3', '4', '5', '6', '7', '8', '9']:
                    # passing
                    self._timercb(t)
                    return True
            if self.curevent is not None:
                return self.curevent.key_event(widget, event)
        return False

    def shutdown(self, msg=''):
        """Shutdown worker threads and close application."""
        self.started = False
        self.announce.exit(msg)
        self.timer.exit(msg)
        self.alttimer.exit(msg)
        _log.info('Waiting for workers')
        if self.mirror is not None:
            _log.debug('Result export')
            self.mirror.join()
            self.mirror = None
        _log.debug('Telegraph/announce')
        self.announce.join()

    def start(self):
        """Start the timer and rfu threads."""
        if not self.started:
            _log.debug('Meet startup')
            self.announce.start()
            self.timer.start()
            self.alttimer.start()
            self.started = True

    ## Roadmeet functions
    def saveconfig(self):
        """Save current meet data to disk."""
        if self.curevent is not None and self.curevent.winopen:
            self.curevent.saveconfig()
        cw = jsonconfig.config()
        cw.add_section('roadmeet')
        cw.set('roadmeet', 'id', ROADMEET_ID)
        cw.set('roadmeet', 'anntopic', self.anntopic)
        cw.set('roadmeet', 'timertopic', self.timertopic)
        cw.set('roadmeet', 'remote_enable', self.remote_enable)
        cw.set('roadmeet', 'timer', self.timer_port)
        cw.set('roadmeet', 'alttimer', self.alttimer_port)
        cw.set('roadmeet', 'shortname', self.shortname)
        cw.set('roadmeet', 'linkbase', self.linkbase)
        cw.set('roadmeet', 'indexlink', self.indexlink)
        cw.set('roadmeet', 'nextlink', self.nextlink)
        cw.set('roadmeet', 'prevlink', self.prevlink)
        cw.set('roadmeet', 'title', self.title_str)
        cw.set('roadmeet', 'host', self.host_str)
        cw.set('roadmeet', 'subtitle', self.subtitle_str)
        cw.set('roadmeet', 'document', self.document_str)
        cw.set('roadmeet', 'date', self.date_str)
        cw.set('roadmeet', 'organiser', self.organiser_str)
        cw.set('roadmeet', 'commissaire', self.commissaire_str)
        cw.set('roadmeet', 'resultnos', self.bibs_in_results)
        cw.set('roadmeet', 'lifexport', self.lifexport)
        cw.set('roadmeet', 'resfiles', self.resfiles)
        cw.set('roadmeet', 'provisionalstart', self.provisionalstart)
        cw.set('roadmeet', 'distance', self.distance)
        cw.set('roadmeet', 'diststr', self.diststr)
        cw.set('roadmeet', 'docindex', self.docindex)
        cw.set('roadmeet', 'mirrorpath', self.mirrorpath)
        cw.set('roadmeet', 'mirrorcmd', self.mirrorcmd)
        cw.set('roadmeet', 'mirrorfile', self.mirrorfile)
        cw.set('roadmeet', 'competitioncode', self.competitioncode)
        cw.set('roadmeet', 'eventcode', self.eventcode)
        cw.set('roadmeet', 'racetype', self.racetype)
        cw.set('roadmeet', 'competitortype', self.competitortype)
        cw.set('roadmeet', 'documentversion', self.documentversion)

        with metarace.savefile(CONFIGFILE) as f:
            cw.write(f)
        self.rdb.save('riders.csv')
        self.edb.save('events.csv')
        _log.info('Meet configuration saved')

    def set_timer(self, newdevice='', force=False):
        """Re-set the main timer device and connect callback."""
        if newdevice != self.timer_port or force:
            self.timer = mkdevice(newdevice, self.timer)
            self.timer_port = newdevice
        else:
            _log.debug('set_timer - No change required')
        self.timer.setcb(self._timercb)

    def loadconfig(self):
        """Load meet config from disk."""
        cr = jsonconfig.config({
            'roadmeet': {
                'shortname': None,
                'title': '',
                'host': '',
                'subtitle': '',
                'document': '',
                'date': '',
                'organiser': '',
                'commissaire': '',
                'distance': None,
                'diststr': '',
                'docindex': '0',
                'timer': '',
                'alttimer': '',
                'resultnos': True,
                'anntopic': None,
                'timertopic': None,
                'remote_enable': False,
                'linkbase': '.',
                'indexlink': None,
                'nextlink': None,
                'prevlink': None,
                'lifexport': False,
                'resfiles': True,
                'provisionalstart': False,
                'mirrorpath': '',
                'mirrorcmd': 'echo',
                'mirrorfile': '',
                'competitioncode': '',
                'eventcode': '',
                'racetype': '',
                'competitortype': '',
                'documentversion': '',
                'id': ''
            }
        })
        cr.add_section('roadmeet')

        # re-set main log file
        _log.debug('Adding meet logfile handler %r', LOGFILE)
        rootlogger = logging.getLogger()
        if self.loghandler is not None:
            rootlogger.removeHandler(self.loghandler)
            self.loghandler.close()
            self.loghandler = None
        self.loghandler = logging.FileHandler(LOGFILE)
        self.loghandler.setLevel(LOGFILE_LEVEL)
        self.loghandler.setFormatter(logging.Formatter(metarace.LOGFILEFORMAT))
        rootlogger.addHandler(self.loghandler)

        cr.merge(metarace.sysconf, 'roadmeet')
        _log.debug('Load system meet defaults')
        cr.load(CONFIGFILE)

        # set timer port (decoder)
        self.set_timer(cr.get('roadmeet', 'timer'))

        # set alt timer port (timy)
        nport = cr.get('roadmeet', 'alttimer')
        if nport != self.alttimer_port:
            self.alttimer_port = nport
            self.alttimer.setport(nport)
            self.alttimer.sane()

        # set the default announce topic and subscribe to control topic
        self.anntopic = cr.get('roadmeet', 'anntopic')
        if self.anntopic:
            self.announce.subscribe('/'.join((self.anntopic, 'control', '#')))

        # fetch the remote timer topic and update remote control
        self.timertopic = cr.get('roadmeet', 'timertopic')
        self.remote_enable = cr.get_bool('roadmeet', 'remote_enable')
        self.remote_reset()

        # set meet meta, and then copy into text entries
        self.shortname = cr.get('roadmeet', 'shortname')
        self.title_str = cr.get('roadmeet', 'title')
        self.host_str = cr.get('roadmeet', 'host')
        self.subtitle_str = cr.get('roadmeet', 'subtitle')
        self.document_str = cr.get('roadmeet', 'document')
        self.date_str = cr.get('roadmeet', 'date')
        self.organiser_str = cr.get('roadmeet', 'organiser')
        self.commissaire_str = cr.get('roadmeet', 'commissaire')
        self.distance = cr.get_float('roadmeet', 'distance')
        self.diststr = cr.get('roadmeet', 'diststr')
        self.docindex = cr.get_posint('roadmeet', 'docindex', 0)
        self.linkbase = cr.get('roadmeet', 'linkbase')
        self.indexlink = cr.get('roadmeet', 'indexlink')
        self.prevlink = cr.get('roadmeet', 'prevlink')
        self.nextlink = cr.get('roadmeet', 'nextlink')
        self.bibs_in_results = cr.get_bool('roadmeet', 'resultnos')
        self.mirrorpath = cr.get('roadmeet', 'mirrorpath')
        self.mirrorcmd = cr.get('roadmeet', 'mirrorcmd')
        self.mirrorfile = cr.get('roadmeet', 'mirrorfile')
        self.competitioncode = cr.get('roadmeet', 'competitioncode')
        self.eventcode = cr.get('roadmeet', 'eventcode')
        self.racetype = cr.get('roadmeet', 'racetype')
        self.competitortype = cr.get('roadmeet', 'competitortype')
        self.documentversion = cr.get('roadmeet', 'documentversion')
        self.lifexport = cr.get_bool('roadmeet', 'lifexport')
        self.resfiles = cr.get_bool('roadmeet', 'resfiles')
        self.provisionalstart = cr.get_bool('roadmeet', 'provisionalstart')

        # Re-Initialise rider and event databases
        self.rdb.clear(notify=False)
        _log.debug('meet load riders from riders.csv')
        self.rdb.load('riders.csv')
        self.edb.clear()
        self.edb.load('events.csv')
        event = self.edb.getfirst()
        if event is None:  # add a new event of the right type
            event = self.edb.add_empty(evno='0')
            event['type'] = self.etype
        else:
            self.etype = event['type']
            _log.debug('Existing event in db: %r', self.etype)
        self.open_event(event)  # always open on load if posible
        self.set_title()

        # alt timer config post event load
        if self.etype == 'irtt':
            self.alttimer.write('DTS05.00')
            self.alttimer.write('DTF00.01')
        else:
            # assume 1 second gaps at finish
            self.alttimer.write('DTF01.00')

        # make sure export path exists
        if not os.path.exists(self.exportpath):
            os.mkdir(self.exportpath)
            _log.info('Created export path: %r', self.exportpath)

        # check and warn of config mismatch
        cid = cr.get('roadmeet', 'id')
        if cid != ROADMEET_ID:
            _log.warning('Meet config mismatch: %r != %r', cid, ROADMEET_ID)

    def get_distance(self):
        """Return race distance in km."""
        return self.distance

    ## Announcer methods (replaces old irc/unt telegraph)
    def cmd_announce(self, command, msg):
        """Announce the supplied message to the command topic."""
        if self.anntopic:
            topic = '/'.join((self.anntopic, command))
            self.announce.publish(msg, topic)

    def rider_announce(self, rvec):
        """Issue a serialised rider vector to announcer."""
        # Deprecated UNT-style list
        self.cmd_announce('rider', '\x1f'.join(rvec))

    def timer_announce(self, evt, timer=None, source=''):
        """Send message into announce for remote control."""
        if not self.remote_enable and self.timertopic is not None:
            if timer is None:
                timer = self.timer
            prec = 4
            if timer is self.timer:
                prec = 3  # transponders have reduced precision
            elif 'M' in evt.chan:
                prec = 3
            if evt.source is not None:
                source = evt.source
            tvec = (evt.index, source, evt.chan, evt.refid, evt.rawtime(prec),
                    '')
            self.announce.publish(';'.join(tvec), self.timertopic)
        self.rfustat.update('activity')
        self.rfuact = True
        return False

    def mirror_start(self):
        """Create a new mirror thread unless already in progress."""
        if self.mirrorpath and self.mirror is None:
            self.mirror = export.mirror(localpath=os.path.join('export', ''),
                                        remotepath=self.mirrorpath,
                                        mirrorcmd=self.mirrorcmd)
            self.mirror.start()
        return False  # for idle_add

    def remote_reset(self):
        """Reset remote input of timer messages."""
        _log.debug('Remote control reset')
        if self.timertopic is not None:
            if self.remote_enable:
                _log.debug('Listening for remote timer at %r', self.timertopic)
                self.announce.subscribe(self.timertopic)
            else:
                _log.debug('Remote timer disabled')
                self.announce.unsubscribe(self.timertopic)
        else:
            _log.debug('Remote timer topic not cofigured')

    def remote_timer(self, msg):
        """Process and dispatch a remote timer message."""
        # 'INDEX;SOURCE;CHANNEL;REFID;TIMEOFDAY;DATE'
        tv = msg.split(';')
        if len(tv) == 5 or len(tv) == 6:
            try:
                if len(tv) > 5:
                    # check date against today
                    # if today != tv[5]:
                    # log and return
                    pass
                tval = tod.mktod(tv[4])
                tval.source = tv[1]
                tval.chan = tv[2]
                tval.refid = tv[3]
                _log.debug('Remote src:%r index:%r chan:%r refid:%r @ %r',
                           tv[1], tv[0], tv[2], tv[3], tval.rawtime())
                if 'timy' in tv[1]:
                    tval.index = tv[0]
                    self._alttimercb(tval)
                else:
                    tval.index = 'REM'
                    self._timercb(tval)
            except Exception as e:
                _log.warning('Error reading timer msg %r: %s', msg, e)
        else:
            _log.debug('Invalid remote timer message: %r', tv)

    def remote_command(self, topic, msg):
        """Handle a remote control message."""
        if topic == self.timertopic:
            if self.remote_enable:
                self.remote_timer(msg)
        else:
            _log.debug('Unsupported remote command %r:%r', topic, msg)
        return False

    def getrefid(self, refid):
        """Return a handle to the rider with the suplied refid or None."""
        ret = None
        refid = refid.lower()
        if u'riderno:' in refid:
            rno, rser = strops.bibstr2bibser(refid.split(':')[-1])
            ret = self.rdb.get_rider(rno, rser)
        if refid in self._tagmap:
            ret = self.rdb[self._tagmap[refid]]
        return ret

    def ridercb(self, rider):
        """Handle a change in the rider model"""
        if rider is not None:
            r = self.rdb[rider]
            otag = None
            ntag = r['refid'].lower()
            if rider in self._maptag:
                otag = self._maptag[rider]
            if otag != ntag:
                if rider in self._maptag:
                    del (self._maptag[rider])
                if otag in self._tagmap:
                    del (self._tagmap[otag])
                if ntag:
                    self._maptag[rider] = ntag
                    self._tagmap[ntag] = rider
                _log.debug('Updated tag map %r = %r', ntag, rider)
            for lr in self._rlm:
                if lr[0] == rider[0] and lr[1] == rider[1]:
                    lr[2] = r.fitname(64)
                    lr[3] = r['org']
                    lr[4] = r['cat']
                    lr[5] = r['refid']
                    lr[6] = r.summary()
                    break
        else:
            # assume entire map has to be rebuilt
            self._tagmap = {}
            self._maptag = {}
            self._rlm.clear()
            for r in self.rdb:
                if r[1] != 'cat':
                    dbr = self.rdb[r]
                    refid = dbr['refid'].lower()
                    if refid:
                        self._tagmap[refid] = r
                        self._maptag[r] = refid
                    rlr = [
                        dbr['bib'], dbr['ser'],
                        dbr.fitname(64), dbr['org'], dbr['cat'], dbr['refid'],
                        dbr.summary()
                    ]
                    self._rlm.append(rlr)
            _log.debug('Re-built refid tagmap: %d entries', len(self._tagmap))
        if self.curevent is not None:
            self.curevent.ridercb(rider)

    def _timercb(self, evt, data=None):
        if self.timercb is not None:
            GLib.idle_add(self.timercb, evt, priority=GLib.PRIORITY_HIGH)
        GLib.idle_add(self.timer_announce, evt, self.timer, 'rfid')

    def _alttimercb(self, evt, data=None):
        if self.alttimercb is not None:
            GLib.idle_add(self.alttimercb, evt, priority=GLib.PRIORITY_HIGH)
        GLib.idle_add(self.timer_announce, evt, self.alttimer, 'timy')

    def _controlcb(self, topic=None, message=None):
        GLib.idle_add(self.remote_command, topic, message)

    def _rcb(self, rider):
        GLib.idle_add(self.ridercb, rider)

    def _editcol_cb(self, cell, path, new_text, col):
        """Callback for editing a transponder ID"""
        new_text = new_text.strip()
        bib = self._rlm[path][0]
        series = self._rlm[path][1]
        r = self.rdb.get_rider(bib, series)
        if r is not None:
            if col == 3:
                r['org'] = new_text
            elif col == 4:
                r['cat'] = new_text.upper()
            elif col == 5:
                r['refid'] = new_text.lower()
            self._rlm[path][col] = new_text

    def __init__(self, etype=None):
        """Meet constructor."""
        self.loghandler = None  # set in loadconfig to meet dir
        self.exportpath = EXPORTPATH
        if etype not in ROADRACE_TYPES:
            etype = 'road'
        self.etype = etype
        self.shortname = None
        self.title_str = ''
        self.host_str = ''
        self.subtitle_str = ''
        self.document_str = ''
        self.date_str = ''
        self.organiser_str = ''
        self.commissaire_str = ''
        self.distance = None
        self.diststr = ''
        self.docindex = 0
        self.linkbase = '.'
        self.provisionalstart = False
        self.indexlink = None
        self.nextlink = None
        self.prevlink = None

        self.bibs_in_results = True
        self.remote_enable = False
        self.lifexport = False
        self.resfiles = True

        # printer preferences
        paper = Gtk.PaperSize.new_custom('metarace-full', 'A4 for reports',
                                         595, 842, Gtk.Unit.POINTS)
        self.printprefs = Gtk.PrintSettings.new()
        self.pageset = Gtk.PageSetup.new()
        self.pageset.set_orientation(Gtk.PageOrientation.PORTRAIT)
        self.pageset.set_paper_size(paper)
        self.pageset.set_top_margin(0, Gtk.Unit.POINTS)
        self.pageset.set_bottom_margin(0, Gtk.Unit.POINTS)
        self.pageset.set_left_margin(0, Gtk.Unit.POINTS)
        self.pageset.set_right_margin(0, Gtk.Unit.POINTS)

        # hardware connections
        self.timertopic = None  # remote timer topic
        self.timer = decoder()
        self.timer_port = ''
        self.timer.setcb(self._timercb)
        self.timercb = None  # set by event app
        self.alttimer = timy()  # alttimer is always timy
        self.alttimer_port = ''
        self.alttimer.setcb(self._alttimercb)
        self.alttimercb = None  # set by event app
        self.announce = telegraph()
        self.announce.setcb(self._controlcb)
        self.anntopic = None
        self.mirrorpath = ''
        self.mirrorcmd = 'echo'
        self.mirrorfile = ''
        self.mirror = None
        self.competitioncode = ''
        self.eventcode = ''
        self.racetype = ''
        self.competitortype = ''
        self.documentversion = ''

        b = uiutil.builder('roadmeet.ui')
        self.window = b.get_object('meet')
        self.window.connect('key-press-event', self.key_event)
        self.rfustat = uiutil.statButton()
        self.rfustat.set_sensitive(True)
        b.get_object('menu_clock').add(self.rfustat)
        self.rfustat.update('idle', '--')
        self.rfuact = False
        self.rfufail = 0
        self.status = b.get_object('status')
        self.log_buffer = b.get_object('log_buffer')
        self.log_view = b.get_object('log_view')
        #self.log_view.modify_font(uiutil.LOGVIEWFONT)
        self.log_scroll = b.get_object('log_box').get_vadjustment()
        self.context = self.status.get_context_id('metarace meet')
        self.menu_race_close = b.get_object('menu_race_close')
        self.menu_race_abort = b.get_object('menu_race_abort')
        self.decoder_configure = b.get_object('menu_timing_configure')
        self.race_box = b.get_object('race_box')
        self.stat_but = uiutil.statButton()
        b.get_object('race_stat_but').add(self.stat_but)
        self.action_model = b.get_object('race_action_model')
        self.action_combo = b.get_object('race_action_combo')
        self.action_entry = b.get_object('race_action_entry')
        b.get_object('race_stat_hbox').set_focus_chain(
            [self.action_combo, self.action_entry, self.action_combo])

        # prepare local scratch pad ? can these be removed?
        self.an_cur_lap = tod.ZERO
        self.an_cur_split = tod.ZERO
        self.an_cur_bunchid = 0
        self.an_cur_bunchcnt = 0
        self.an_last_time = None
        self.an_cur_start = tod.ZERO

        b.connect_signals(self)

        # run state
        self.running = True
        self.started = False
        self.curevent = None

        # connect UI log handlers
        _log.debug('Connecting interface log handlers')
        rootlogger = logging.getLogger()
        f = logging.Formatter(metarace.LOGFORMAT)
        self.sh = uiutil.statusHandler(self.status, self.context)
        self.sh.setFormatter(f)
        self.sh.setLevel(logging.INFO)  # show info+ on status bar
        rootlogger.addHandler(self.sh)
        self.lh = uiutil.textViewHandler(self.log_buffer, self.log_view,
                                         self.log_scroll)
        self.lh.setFormatter(f)
        self.lh.setLevel(logging.INFO)  # show info+ in text view
        rootlogger.addHandler(self.lh)

        # Build a rider list store and view
        self._rlm = Gtk.ListStore(
            str,  # no 0
            str,  # series 1
            str,  # name 2 
            str,  # org 3
            str,  # categories 4
            str,  # Refid 5
            str,  # tooltip 6
        )
        t = Gtk.TreeView(self._rlm)
        t.set_reorderable(True)
        t.set_rules_hint(True)
        t.set_tooltip_column(6)
        uiutil.mkviewcoltxt(t, 'No.', 0, calign=1.0)
        uiutil.mkviewcoltxt(t, 'Ser', 1, calign=0.0)
        uiutil.mkviewcoltxt(t, 'Rider', 2, expand=True)
        uiutil.mkviewcoltxt(t, 'Org', 3, cb=self._editcol_cb)
        uiutil.mkviewcoltxt(t, 'Cats', 4, width=80, cb=self._editcol_cb)
        uiutil.mkviewcoltxt(t, 'Refid', 5, width=80, cb=self._editcol_cb)
        t.show()
        b.get_object('riders_box').add(t)

        # get rider db
        _log.debug('Add riderdb and eventdb')
        self.rdb = riderdb.riderdb()
        self.rdb.set_notify(self._rcb)
        self._tagmap = {}
        self._maptag = {}

        ## get event db -> loadconfig adds empty event if one not present
        self.edb = eventdb.eventdb([])

        # select event page in notebook.
        b.get_object('meet_nb').set_current_page(0)

        # start timer
        GLib.timeout_add_seconds(1, self.timeout)


def main():
    """Run the road meet application as a console script."""
    # attach a console log handler to the root logger then run the ui
    ch = logging.StreamHandler()
    ch.setLevel(metarace.LOGLEVEL)
    fh = logging.Formatter(metarace.LOGFORMAT)
    ch.setFormatter(fh)
    logging.getLogger().addHandler(ch)

    metarace.init()
    configpath = metarace.DATA_PATH
    if len(sys.argv) > 2:
        _log.error('Usage: roadmeet [configdir]')
        sys.exit(1)
    elif len(sys.argv) == 2:
        configpath = sys.argv[1]
    configpath = metarace.config_path(configpath)
    if configpath is None:
        _log.error('Unable to open meet config %r', sys.argv[1])
        sys.exit(1)
    app = runapp(configpath)
    return Gtk.main()


def runapp(configpath, etype=None):
    """Create the roadmeet object, start in configpath and return a handle."""
    lf = metarace.lockpath(configpath)
    if lf is None:
        _log.error('Unable to lock meet config, already in use')
        sys.exit(1)
    _log.debug('Entering meet folder %r', configpath)
    os.chdir(configpath)
    app = roadmeet(etype)
    # do the old mr library UI tweaks and set logo
    app.loadconfig()
    # try to set the menubar accel and logo
    try:
        with metarace.resource_file(metarace.LOGO) as f:
            Gtk.Window.set_default_icon_from_file(str(f))
        mset = Gtk.Settings.get_default()
        mset.set_property('gtk-menu-bar-accel', 'F24')
    except Exception as e:
        _log.debug('%s setting property: %s', e.__class__.__name__, e)
    app.window.show()
    app.start()
    return app


if __name__ == '__main__':
    main()
