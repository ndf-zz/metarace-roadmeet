"""Timing and data handling application wrapper for road events."""

import sys
import gi
import logging
import metarace
from metarace import htlib
import csv
import os
from time import sleep

gi.require_version("GLib", "2.0")
from gi.repository import GLib

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

from metarace import jsonconfig
from metarace import tod
from metarace import riderdb
from metarace.telegraph import telegraph, _CONFIG_SCHEMA as _TG_SCHEMA
from metarace.export import mirror, _CONFIG_SCHEMA as _EXPORT_SCHEMA
from metarace.decoder import decoder
from metarace.decoder.rru import rru, _CONFIG_SCHEMA as _RRU_SCHEMA
from metarace.decoder.rrs import rrs, _CONFIG_SCHEMA as _RRS_SCHEMA
from metarace.decoder.thbc import thbc, _CONFIG_SCHEMA as _THBC_SCHEMA
from metarace.timy import timy, _CONFIG_SCHEMA as _TIMY_SCHEMA
from metarace import strops
from metarace import report

from . import uiutil
from roadmeet.rms import rms, _CONFIG_SCHEMA as _RMS_SCHEMA
from roadmeet.irtt import irtt, _CONFIG_SCHEMA as _IRTT_SCHEMA
from roadmeet.trtt import trtt, _CONFIG_SCHEMA as _TRTT_SCHEMA

VERSION = '1.13.4'
LOGFILE = 'event.log'
LOGFILE_LEVEL = logging.DEBUG
CONFIGFILE = 'config.json'
ROADMEET_ID = 'roadmeet-3.2'  # configuration versioning
EXPORTPATH = 'export'
_log = logging.getLogger('roadmeet')
_log.setLevel(logging.DEBUG)
ROADRACE_TYPES = {
    'road': 'Road Race',
    'circuit': 'Circuit',
    'criterium': 'Criterium',
    'handicap': 'Handicap',
    'cross': 'Cyclocross',
    'irtt': 'Individual Time Trial',
    'trtt': 'Team Time Trial',
}
_HANDLERS = {
    'null': decoder,
    'thbc': thbc,
    'rrs': rrs,
    'rru': rru,
}
_CONFIG_SCHEMA = {
    'mtype': {
        'prompt': 'Meet Information',
        'control': 'section',
    },
    'etype': {
        'prompt': 'Type:',
        'control': 'choice',
        'attr': 'etype',
        'defer': True,
        'options': ROADRACE_TYPES,
        'default': 'road',
    },
    'title': {
        'prompt': 'Title:',
        'hint': 'Meet title',
        'attr': 'title',
        'default': '',
    },
    'subtitle': {
        'prompt': 'Subtitle:',
        'hint': 'Meet subtitle',
        'attr': 'subtitle',
        'default': '',
    },
    'host': {
        'prompt': 'Host:',
        'hint': 'Text for the meet host / sponsor line',
        'attr': 'host',
        'default': '',
    },
    'document': {
        'prompt': 'Location:',
        'hint': 'Text for the meet location / document line',
        'attr': 'document',
        'default': '',
    },
    'date': {
        'prompt': 'Date:',
        'hint': 'Date of the meet as human-readable text',
        'attr': 'date',
        'default': '',
    },
    'pcp': {
        'prompt': 'PCP:',
        'hint': 'Name of the president of the commissaires panel',
        'attr': 'pcp',
        'default': '',
    },
    'organiser': {
        'prompt': 'Organiser:',
        'hint': 'Name of the meet organiser',
        'attr': 'organiser',
        'default': '',
    },
    'distance': {
        'prompt': 'Distance:',
        'hint': 'Advertised distance of the meet (if applicable)',
        'type': 'float',
        'control': 'short',
        'subtext': 'km',
        'attr': 'distance'
    },
    'diststr': {
        'prompt': 'Dist String:',
        'hint': 'Override distance string for crit/cat races',
        'attr': 'diststr',
        'default': '',
    },
    'provisionalstart': {
        'prompt': 'Startlist:',
        'control': 'check',
        'type': 'bool',
        'subtext': 'Provisional?',
        'hint': 'Mark startlist reports as provisional',
        'attr': 'provisionalstart',
        'default': True,
    },
    'sectele': {
        'control': 'section',
        'prompt': 'Telegraph',
    },
    'anntopic': {
        'prompt': 'Announce:',
        'hint': 'Base topic for announcer messages',
        'attr': 'anntopic',
    },
    'announceresult': {
        'prompt': 'Announce Result:',
        'control': 'check',
        'type': 'bool',
        'subtext': 'Publish result to telegraph?',
        'hint': 'Announce result to telegraph on export',
        'attr': 'announceresult',
        'default': False,
    },
    'timertopic': {
        'prompt': 'Timer:',
        'hint': 'Full topic for timer messages',
        'attr': 'timertopic',
    },
    'remoteenable': {
        'prompt': 'Option:',
        'control': 'check',
        'type': 'bool',
        'subtext': 'Receive remote timer messages?',
        'hint': 'Receive remote timer messages from timer topic',
        'attr': 'remoteenable',
        'default': False,
    },
    'sechw': {
        'control': 'section',
        'prompt': 'Hardware',
    },
    'timer': {
        'prompt': 'Transponders:',
        'hint': 'Transponder decoder spec TYPE:ADDR, eg: rrs:10.1.2.3',
        'defer': True,
        'attr': 'timer',
    },
    'alttimer': {
        'prompt': 'Impulse:',
        'hint': 'Impulse timer port eg: /dev/ttyS0',
        'defer': True,
        'attr': 'alttimer'
    },
    'secexp': {
        'control': 'section',
        'prompt': 'Export',
    },
    'mirrorcmd': {
        'prompt': 'Command:',
        'hint': 'Command to run if export script is enabled',
        'attr': 'mirrorcmd',
    },
    'mirrorpath': {
        'prompt': 'Path:',
        'hint': 'Result export path',
        'attr': 'mirrorpath',
    },
    'mirrorfile': {
        'prompt': 'Filename:',
        'hint': 'Result export filename prefix',
        'attr': 'mirrorfile',
    },
    'shortname': {
        'prompt': 'Short Name:',
        'hint': 'Short meet name on web export header',
        'attr': 'shortname',
    },
    'eventcode': {
        'prompt': 'Event Code:',
        'hint': 'Event code saved in reports',
        'attr': 'eventcode',
    },
    'resfiles': {
        'prompt': 'Result Files:',
        'control': 'check',
        'type': 'bool',
        'subtext': 'Build results on export?',
        'hint': 'Build result files with export',
        'attr': 'resfiles',
        'default': True,
    },
    'lifexport': {
        'prompt': 'LIF Export:',
        'control': 'check',
        'type': 'bool',
        'subtext': 'Build LIF file on export?',
        'hint': 'Export LIF result file with results',
        'attr': 'lifexport',
        'default': False,
    },
    # the following are currently used for html export, but are likely
    # to be removed in later versions
    'linkbase': {
        'attr': 'linkbase',
        'control': 'none',
        'default': '.',
    },
    'indexlink': {
        'attr': 'indexlink',
        'control': 'none',
    },
    'prevlink': {
        'attr': 'prevlink',
        'control': 'none',
    },
    'nextlink': {
        'attr': 'nextlink',
        'control': 'none',
    },
}


def mkdevice(portstr=None, curdev=None):
    """Return a decoder handle for the provided port specification."""
    # Note: If possible, returns the current device
    if portstr is None:
        portstr = ''
    ret = curdev
    devtype = 'null'
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

    def menu_event_decisions_activate_cb(self, menuitem, data=None):
        """Edit decisions of the commissaires panel."""
        if self.curevent is not None:
            self.curevent.decisions = uiutil.decisions_dlg(
                self.window, self.curevent.decisions)

    def menu_event_properties_activate_cb(self, menuitem, data=None):
        """Edit event specific properties."""
        if self.curevent is not None:
            _log.debug('Editing event properties')
            if self.curevent.edit_event_properties(self.window):
                _log.info('Event re-start required')
                self.event_reload()

    def menu_event_reset_cb(self, menuitem, data=None):
        """Reset current event."""
        if self.curevent is not None:
            _log.debug('Reset event')
            if uiutil.questiondlg(
                    window=self.window,
                    question='Reset event to idle?',
                    subtext='Note: All result and timing data will be cleared.',
                    title='Reset Event?'):
                self.curevent.resettimer()

    def menu_meet_properties_cb(self, menuitem, data=None):
        """Edit meet properties."""
        metarace.sysconf.add_section('export', _EXPORT_SCHEMA)
        metarace.sysconf.add_section('telegraph', _TG_SCHEMA)
        metarace.sysconf.add_section('thbc', _THBC_SCHEMA)
        metarace.sysconf.add_section('rru', _RRU_SCHEMA)
        metarace.sysconf.add_section('rrs', _RRS_SCHEMA)
        metarace.sysconf.add_section('timy', _TIMY_SCHEMA)
        cfgres = uiutil.options_dlg(window=self.window,
                                    title='Meet Properties',
                                    sections={
                                        'meet': {
                                            'title': 'Meet',
                                            'schema': _CONFIG_SCHEMA,
                                            'object': self,
                                        },
                                        'export': {
                                            'title': 'Export',
                                            'schema': _EXPORT_SCHEMA,
                                            'object': metarace.sysconf,
                                        },
                                        'telegraph': {
                                            'title': 'Telegraph',
                                            'schema': _TG_SCHEMA,
                                            'object': metarace.sysconf,
                                        },
                                        'timy': {
                                            'title': 'Timy',
                                            'schema': _TIMY_SCHEMA,
                                            'object': metarace.sysconf,
                                        },
                                        'thbc': {
                                            'title': 'THBC',
                                            'schema': _THBC_SCHEMA,
                                            'object': metarace.sysconf,
                                        },
                                        'rru': {
                                            'title': 'RR USB',
                                            'schema': _RRU_SCHEMA,
                                            'object': metarace.sysconf,
                                        },
                                        'rrs': {
                                            'title': 'RR System',
                                            'schema': _RRS_SCHEMA,
                                            'object': metarace.sysconf,
                                        },
                                    })

        # check for sysconf changes:
        syschange = False
        timychg = False
        timerchg = False
        tgchg = False
        for sec in ('export', 'timy', 'rru', 'rrs', 'telegraph', 'thbc'):
            for key in cfgres[sec]:
                if cfgres[sec][key][0]:
                    syschange = True
                    if sec == 'telegraph':
                        tgchg = True
                    elif sec in ('rru', 'rrs', 'thbc'):
                        timerchg = True
                    elif sec == 'timy':
                        timerchg = True
                        timychg = True
        if syschange:
            _log.info('Saving config updates to meet folder')
            with metarace.savefile(metarace.SYSCONF, perm=0o600) as f:
                metarace.sysconf.write(f)

        # reset telegraph connection if required
        if tgchg:
            _log.info('Re-start telegraph')
            newannounce = telegraph()
            newannounce.setcb(self._controlcb)
            newannounce.start()
            oldannounce = self.announce
            self.announce = newannounce
            oldannounce.exit()

        # reset alttimer connection if required
        if timychg:
            _log.info('Re-start alt timer')
            newtimy = timy()
            newtimy.setcb(self._alttimercb)
            newtimy.start()
            oldtimy = self._alttimer
            self._alttimer = newtimy
            oldtimy.exit()

        res = cfgres['meet']
        # handle a change in announce topic
        if res['anntopic'][0] or tgchg:
            otopic = res['anntopic'][1]
            if otopic:
                self.announce.unsubscribe('/'.join((otopic, 'control', '#')))
            if self.anntopic:
                self.announce.subscribe('/'.join(
                    (self.anntopic, 'control', '#')))

        # handle change in timer topic
        if res['timertopic'][0] or tgchg:
            otopic = res['timertopic'][1]
            if otopic:
                self.announce.unsubscribe(otopic)

        # reset remote option
        if res['timertopic'][0] or res['remoteenable'][0] or tgchg:
            self.remote_reset()

        # if type has changed, backup config and reload
        if res['etype'][0]:
            timerchg = True
            reopen = False
            if self.curevent is not None:
                reopen = True
                conf = self.curevent.configfile
                self.close_event()
                backup = conf + '.bak'
                _log.warning('Event type change, backing up old config to %r',
                             backup)
                try:
                    if os.path.exists(backup):
                        os.unlink(backup)
                    os.link(conf, backup)
                except Exception as e:
                    _log.warning('%s saving event backup: %s',
                                 e.__class__.__name__, e)
            if reopen:
                self.open_event()

        # reset timer ports
        if res['timer'][0] or res['alttimer'][0] or timerchg:
            self.menu_timing_reconnect_activate_cb(None)

        self.set_title()

    def report_strings(self, rep):
        """Copy meet information into the supplied report."""
        rep.strings['title'] = self.title
        rep.strings['subtitle'] = self.subtitle
        rep.strings['host'] = self.host
        rep.strings['docstr'] = self.document
        rep.strings['datestr'] = strops.promptstr('Date:', self.date)
        rep.strings['commstr'] = strops.promptstr('PCP:', self.pcp)
        rep.strings['orgstr'] = strops.promptstr('Organiser:', self.organiser)
        diststr = self.diststr
        if not diststr:
            if self.distance:
                diststr = strops.promptstr('Distance:',
                                           '%0.1f\u2006km' % (self.distance))
        rep.strings['diststr'] = diststr
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

    def print_report(self, sections=[], provisional=False):
        """Print the pre-formatted sections in a standard report."""
        rep = report.report()
        rep.provisional = provisional
        self.report_strings(rep)
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

    def event_reload(self):
        """Open the event handler."""
        self.open_event()
        self.set_title()
        return False

    def menu_event_armstart_activate_cb(self, menuitem, data=None):
        """Default armstart handler."""
        _log.info('Arm Start')
        try:
            self.curevent.armstart()
        except Exception as e:
            _log.error('Arm start %s: %s', e.__class__.__name__, e)

    def menu_event_armlap_activate_cb(self, menuitem, data=None):
        """Default armlap handler."""
        _log.debug('Arm Lap')
        try:
            self.curevent.armlap()
        except Exception as e:
            _log.error('Arm lap %s: %s', e.__class__.__name__, e)

    def menu_event_armfin_activate_cb(self, menuitem, data=None):
        """Default armfin handler."""
        _log.info('Arm Finish')
        try:
            self.curevent.armfinish()
        except Exception as e:
            _log.error('Arm finish %s: %s', e.__class__.__name__, e)

    def menu_event_finished_activate_cb(self, menuitem, data=None):
        """Default finished handler."""
        _log.info('Finished')
        try:
            self.curevent.set_finished()
        except Exception as e:
            _log.error('Set finished %s: %s', e.__class__.__name__, e)

    def open_event(self):
        """Open provided event handle."""
        self.close_event()
        if self.etype not in ROADRACE_TYPES:
            _log.warning('Unknown event type %r', self.etype)
        if self.etype == 'irtt':
            self.curevent = irtt(self, self.etype, True)
        elif self.etype == 'trtt':
            self.curevent = trtt(self, self.etype, True)
        else:
            self.curevent = rms(self, self.etype, True)

        self.curevent.loadconfig()
        self.event_box.add(self.curevent.frame)

        # re-populate the rider command model.
        cmdo = self.curevent.get_ridercmdorder()
        cmds = self.curevent.get_ridercmds()
        if cmds is not None:
            self.action_model.clear()
            for cmd in cmdo:
                self.action_model.append([cmd, cmds[cmd]])
            self.action_combo.set_active(0)
        self.curevent.show()

    def close_event(self):
        """Close the currently opened event."""
        if self.curevent is not None:
            if self.curevent.frame in self.event_box.get_children():
                self.event_box.remove(self.curevent.frame)
            self.curevent.destroy()
            self.curevent = None
            self.stat_but.update('idle', 'Closed')
            self.stat_but.set_sensitive(False)

    ## Reports menu callbacks.
    def menu_reports_startlist_activate_cb(self, menuitem, data=None):
        """Generate a startlist."""
        if self.curevent is not None:
            sections = self.curevent.startlist_report()
            if sections:
                self.print_report(sections, provisional=self.provisionalstart)
            else:
                _log.info('Startlist - Nothing to print')

    def menu_reports_callup_activate_cb(self, menuitem, data=None):
        """Generate a start line call-up."""
        if self.curevent is not None:
            sections = self.curevent.callup_report()
            if sections:
                self.print_report(sections, provisional=self.provisionalstart)
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

    def event_results_points_activate_cb(self, menuitem, data=None):
        """Generate the points tally report."""
        if self.curevent is not None:
            sections = self.curevent.points_report()
            if sections:
                self.print_report(sections)

    def menu_reports_result_activate_cb(self, menuitem, data=None):
        """Generate the event result report."""
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
            except Exception as e:
                _log.error('%s loading riders: %s', e.__class__.__name__, e)
        else:
            _log.debug('Import riders cancelled')

    def menu_data_clear_activate_cb(self, menuitem, data=None):
        """Clear rider db."""
        self.rdb.clear()
        _log.info('Cleared rider db')

    def menu_import_riders_activate_cb(self, menuitem, data=None):
        """Add riders to database."""
        sfile = uiutil.chooseCsvFile(title='Select rider file to import',
                                     parent=self.window,
                                     path='.')
        if sfile is not None:
            try:
                count = self.rdb.load(sfile, overwrite=True)
                _log.info('Imported %d rider entries from %r', count, sfile)
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
        sfile = uiutil.chooseCsvFile(title='Select startlist file to import',
                                     parent=self.window,
                                     path='.')
        self.import_starters(sfile)

    def import_starters(self, sfile):
        """Import starters from the nominated csvfile"""
        if os.path.isfile(sfile):
            count = 0
            with open(sfile, encoding='utf-8', errors='replace') as f:
                cr = csv.reader(f)
                for r in cr:
                    if len(r) > 1 and r[1].isalnum() and r[1].lower() not in (
                            'no', 'no.'):
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
        if filebase in ('', '.'):
            filebase = ''
            if self.resfiles:
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
        if self.resfiles and self.curevent.timerstat != 'finished':
            filename = sfile
            rep = report.report()
            self.report_strings(rep)
            if self.provisionalstart:
                rep.set_provisional(True)
            if self.prevlink:
                rep.prevlink = '_'.join((self.prevlink, 'startlist'))
            if self.nextlink:
                rep.nextlink = '_'.join((self.nextlink, 'startlist'))
            rep.resultlink = ffile
            if self.etype == 'irtt':
                for sec in self.curevent.callup_report():
                    rep.add_section(sec)
            else:
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
        self.report_strings(rep)

        # Set provisional status
        if self.curevent.timerstat != 'finished':
            rep.set_provisional(True)
        else:
            rep.reportstatus = 'final'
        for sec in self.curevent.result_report():
            rep.add_section(sec)
        filename = ffile
        rep.startlink = sfile
        if self.prevlink:
            rep.prevlink = '_'.join((self.prevlink, 'result'))
        if self.nextlink:
            rep.nextlink = '_'.join((self.nextlink, 'result'))
        lb = os.path.join(self.linkbase, filename)
        lt = ['pdf', 'xls']
        rep.canonical = '.'.join([lb, 'json'])

        # announce to telegraph if enabled
        if self.announceresult:
            self.obj_announce(command='result', obj=rep.serialise())

        # then dump out files
        if self.resfiles:
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
        if self.resfiles or self.announceresult:
            self.export_result_maker()
        GLib.idle_add(self.mirror_start)

    ## Timing menu callbacks
    def menu_timing_status_cb(self, menuitem, data=None):
        if self.timer:
            if self._timer.connected():
                _log.info('Request timer status')
                self._timer.status()
            else:
                _log.info('Decoder disconnected')
        else:
            _log.info('No decoder configured')
        # always call into alt timer
        self._alttimer.status()

    def menu_timing_start_activate_cb(self, menuitem, data=None):
        """Manually set event elapsed time via trigger."""
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
        self._timer.clear()
        self._alttimer.clrmem()

    def set_altchannels(self):
        self._alttimer.armlock()  # lock the arm to capture all hits
        self._alttimer.arm(0)  # start line
        self._alttimer.arm(1)  # finish line (primary)
        self._alttimer.dearm(6)
        self._alttimer.dearm(7)
        self._alttimer.dearm(8)
        if self.etype == 'irtt':
            self._alttimer.write('DTS05.00')
            self._alttimer.write('DTF00.01')
            self._alttimer.arm(2)  # finish line (photo cell)
            self._alttimer.arm(3)  # finish line (plunger)
            self._alttimer.arm(4)  # start line (backup)
            self._alttimer.arm(5)  # spare
        else:
            self._alttimer.write('DTS00.01')
            if self.etype == 'trtt':
                self._alttimer.write('DTF00.01')
            else:
                # assume 1 second gaps at finish for road types
                self._alttimer.write('DTF01.00')
            self._alttimer.dearm(2)
            self._alttimer.dearm(3)
            self._alttimer.dearm(4)
            self._alttimer.dearm(5)

    def menu_timing_reconnect_activate_cb(self, menuitem, data=None):
        """Drop current timer connection and re-connect"""
        self.set_timer(self.timer, force=True)
        self._alttimer.setport(self.alttimer)
        self._alttimer.sane()
        self.set_altchannels()
        _log.info('Re-connect/re-start attached timers')

    def restart_decoder(self, data=None):
        """Request re-start of decoder."""
        self._timer.start_session()
        return None

    def menu_timing_configure_activate_cb(self, menuitem, data=None):
        """Attempt to re-configure the attached decoder from saved config."""
        if self._timer.__class__.__name__ == 'thbc':
            if not self._timer.connected():
                _log.info('Timer not connected, config not possible')
                return False
            if not uiutil.questiondlg(
                    window=self.window,
                    question='Re-configure THBC Decoder IP Settings?',
                    subtext=
                    'Note: Passings will not be captured while decoder is updating.',
                    title='Update Decoder IP?'):
                _log.debug('Config aborted')
                return False
            self._timer.stop_session()
            self._timer.sane()
            GLib.timeout_add_seconds(60, self.restart_decoder)
            self._timer.ipconfig()
        else:
            _log.info('Decoder config not available')
        return None

    ## Help menu callbacks
    def menu_help_about_cb(self, menuitem, data=None):
        """Display metarace about dialog."""
        uiutil.about_dlg(self.window, VERSION)

    ## Race Control Elem callbacks
    def event_stat_but_clicked_cb(self, button, data=None):
        """Call through into event if open."""
        if self.curevent is not None:
            self.curevent.stat_but_clicked(button)

    def event_stat_entry_activate_cb(self, entry, data=None):
        """Pass the chosen action and bib list through to curevent."""
        action = self.action_model.get_value(
            self.action_combo.get_active_iter(), 0)
        if self.curevent is not None:
            if self.curevent.event_ctrl(action, self.action_entry.get_text()):
                self.action_entry.set_text('')

    ## Menu button callbacks
    def event_action_combo_changed_cb(self, combo, data=None):
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
        try:
            # check for completion in the export thread
            if self.mirror is not None:
                if not self.mirror.is_alive():
                    self.mirror = None
                    _log.debug('Removing completed export thread.')

            if self.running:
                # call into event timeout handler
                if self.curevent is not None:
                    self.curevent.timeout()

                # update the menu status button
                nt = tod.now().meridiem()
                if self.rfuact:
                    self.rfustat.update('activity', nt)
                else:
                    if self.timer:
                        if self._timer.connected():
                            self.rfustat.update('ok', nt)
                        else:
                            self.rfustat.update('error', nt)
                    else:
                        self.rfustat.update('idle', nt)
                self.rfuact = False

                # attempt to heal a broken link
                if self.timer:
                    if self._timer.connected():
                        self.rfufail = 0
                    else:
                        self.rfufail += 1
                        if self.rfufail > 20:
                            self.rfufail = 0
                            eport = self.timer.split(':', 1)[-1]
                            self._timer.setport(eport)
                else:
                    self.rfufail = 0
            else:
                return False
        except Exception as e:
            _log.critical('%s in meet timeout: %s', e.__class__.__name__, e)
        return True

    ## Window methods
    def set_title(self, extra=''):
        """Update window title from meet properties."""
        tv = []
        if self.etype in ROADRACE_TYPES:
            tv.append(ROADRACE_TYPES[self.etype] + ':')

        title = self.title.strip()
        if title:
            tv.append(title)
        subtitle = self.subtitle.strip()
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
        """Collect key events on main window and send to event."""
        if event.type == Gdk.EventType.KEY_PRESS:
            key = Gdk.keyval_name(event.keyval) or 'None'
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                key = key.lower()
                t = tod.now(chan='MAN', refid=str(key))
                if key in ('0', '1'):
                    # trigger
                    t.refid = ''
                    t.chan = strops.id2chan(strops.chan2id(key))
                    self._alttimercb(t)
                    return True
                elif key in ('2', '3', '4', '5', '6', '7', '8', '9'):
                    # passing
                    self._timercb(t)
                    return True
                elif key == 'left':
                    self.notebook.prev_page()
                elif key == 'right':
                    self.notebook.next_page()
            if self.curevent is not None:
                return self.curevent.key_event(widget, event)
        return False

    def shutdown(self, msg=''):
        """Shutdown worker threads and close application."""
        self.started = False
        self.announce.exit(msg)
        self._timer.exit(msg)
        self._alttimer.exit(msg)
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
            self._timer.start()
            self._alttimer.start()
            self.started = True

    ## Roadmeet functions
    def saveconfig(self):
        """Save current meet data to disk."""
        if self.curevent is not None and self.curevent.winopen:
            self.curevent.saveconfig()
        cw = jsonconfig.config()
        cw.add_section('roadmeet', _CONFIG_SCHEMA)
        cw.import_section('roadmeet', self)
        cw.set('roadmeet', 'id', ROADMEET_ID)
        with metarace.savefile(CONFIGFILE) as f:
            cw.write(f)
        self.rdb.save('riders.csv')
        _log.info('Meet configuration saved')

    def set_timer(self, newdevice='', force=False):
        """Re-set the main timer device and connect callback."""
        if newdevice != self.timer or force:
            self._timer = mkdevice(newdevice, self._timer)
            self.timer = newdevice
        else:
            _log.debug('set_timer - No change required')
        self._timer.setcb(self._timercb)

    def loadconfig(self):
        """Load meet config from disk."""
        cr = jsonconfig.config()
        cr.add_section('roadmeet', _CONFIG_SCHEMA)

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
        cr.load(CONFIGFILE)
        cr.export_section('roadmeet', self)

        # update hardware ports and telegraph setting
        self.set_timer(self.timer, force=True)
        if self.alttimer:
            self._alttimer.setport(self.alttimer)
            self._alttimer.sane()
            self.set_altchannels()
        if self.anntopic:
            self.announce.subscribe('/'.join((self.anntopic, 'control', '#')))
        self.remote_reset()

        # Re-Initialise rider database
        self.rdb.clear(notify=False)
        _log.debug('meet load riders from riders.csv')
        self.rdb.load('riders.csv')

        # Open the event
        self.open_event()
        self.set_title()

        # make sure export path exists
        if not os.path.exists(self.exportpath):
            os.mkdir(self.exportpath)
            _log.info('Created export path: %r', self.exportpath)

        # check and warn of config mismatch
        cid = cr.get_value('roadmeet', 'id')
        if cid is not None and cid != ROADMEET_ID:
            _log.warning('Meet config mismatch: %r != %r', cid, ROADMEET_ID)

    def get_distance(self):
        """Return meet distance in km."""
        return self.distance

    ## Announcer methods (replaces old irc/unt telegraph)
    def cmd_announce(self, command, msg):
        """Announce the supplied message to the command topic."""
        if self.anntopic:
            topic = '/'.join((self.anntopic, command))
            self.announce.publish(msg, topic)

    def obj_announce(self, command, obj):
        """Publish obj to command as JSON"""
        if self.anntopic:
            topic = '/'.join((self.anntopic, command))
            self.announce.publish_json(obj, topic)

    def rider_announce(self, rvec, command='rider'):
        """Issue a serialised rider vector to announcer."""
        # Deprecated UNT-style list
        self.cmd_announce(command, '\x1f'.join(rvec))

    def timer_announce(self, evt, timer=None, source=''):
        """Send message into announce for remote control."""
        if not self.remoteenable and self.timertopic is not None:
            if timer is None:
                timer = self._timer
            prec = 4
            if timer is self._timer:
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
            self.mirror = mirror(localpath=os.path.join(EXPORTPATH, ''),
                                 remotepath=self.mirrorpath,
                                 mirrorcmd=self.mirrorcmd)
            self.mirror.start()
        return False  # for idle_add

    def remote_reset(self):
        """Reset remote input of timer messages."""
        _log.debug('Remote control reset')
        if self.timertopic is not None:
            if self.remoteenable:
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
            if self.remoteenable:
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
            # note: duplicate ids mangle series, so use series from rider
            series = r['series'].lower()
            if series != 'cat':
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
                found = False
                for lr in self._rlm:
                    if lr[7] == rider:
                        lr[2] = r.fitname(64)
                        lr[3] = r['org']
                        lr[4] = r['cat']
                        lr[5] = r['refid']
                        lr[6] = htlib.escape(r.summary())
                        found = True
                        break
                if not found:
                    lr = [
                        rider[0], series,
                        r.fitname(64), r['org'], r['cat'], r['refid'],
                        htlib.escape(r.summary()), rider
                    ]
                    self._rlm.append(lr)
            else:
                found = False
                for lr in self._clm:
                    if lr[7] == rider:
                        lr[1] = r['title']
                        lr[2] = r['subtitle']
                        lr[3] = r['footer']
                        lr[4] = r['target']
                        lr[5] = r['distance']
                        lr[6] = r['start']
                        found = True
                        break
                if not found:
                    lr = [
                        rider[0], r['title'], r['subtitle'], r['footer'],
                        r['target'], r['distance'], r['start'], rider
                    ]
                    self._clm.append(lr)
        else:
            # assume entire map has to be rebuilt
            self._tagmap = {}
            self._maptag = {}
            self._rlm.clear()
            self._clm.clear()
            for r in self.rdb:
                dbr = self.rdb[r]
                # note: duplicate ids mangle series, so use series from rider
                series = dbr['series'].lower()
                if series != 'cat':
                    refid = dbr['refid'].lower()
                    if refid:
                        self._tagmap[refid] = r
                        self._maptag[r] = refid
                    rlr = [
                        r[0], series,
                        dbr.fitname(64), dbr['org'], dbr['cat'], dbr['refid'],
                        htlib.escape(dbr.summary()), r
                    ]
                    self._rlm.append(rlr)
                else:
                    rlr = [
                        r[0], dbr['title'], dbr['subtitle'], dbr['footer'],
                        dbr['target'], dbr['distance'], dbr['start'], r
                    ]
                    self._clm.append(rlr)
            _log.debug('Re-built refid tagmap: %d entries', len(self._tagmap))
        if self.curevent is not None:
            self.curevent.ridercb(rider)

    def _timercb(self, evt, data=None):
        if self.timercb is not None:
            GLib.idle_add(self.timercb, evt, priority=GLib.PRIORITY_HIGH)
        GLib.idle_add(self.timer_announce, evt, self._timer, 'rfid')

    def _alttimercb(self, evt, data=None):
        if self.alttimercb is not None:
            GLib.idle_add(self.alttimercb, evt, priority=GLib.PRIORITY_HIGH)
        GLib.idle_add(self.timer_announce, evt, self._alttimer, 'timy')

    def _controlcb(self, topic=None, message=None):
        GLib.idle_add(self.remote_command, topic, message)

    def _rcb(self, rider):
        GLib.idle_add(self.ridercb, rider)

    def _catcol_cb(self, cell, path, new_text, col):
        """Callback for editing category info"""
        new_text = new_text.strip()
        bib = self._clm[path][0]
        self._clm[path][col] = new_text
        r = self.rdb.get_rider(bib, 'cat')
        if r is not None:
            if col == 1:
                if new_text != r['title']:
                    r['title'] = new_text
            elif col == 2:
                if new_text != r['subtitle']:
                    r['subtitle'] = new_text
            elif col == 3:
                if new_text != r['footer']:
                    r['footer'] = new_text
            elif col == 4:
                if new_text != r['target']:
                    nt = strops.confopt_posint(new_text, None)
                    if nt is not None:
                        r['target'] = str(nt)
            elif col == 5:
                if new_text != r['distance']:
                    nt = strops.confopt_float(new_text, None)
                    if nt is not None:
                        r['distance'] = str(nt)
            elif col == 6:
                if new_text != r['start']:
                    nt = tod.mktod(new_text)
                    if nt is not None:
                        r['start'] = nt.rawtime(0)

    def _editcol_cb(self, cell, path, new_text, col):
        """Callback for editing a transponder ID"""
        new_text = new_text.strip()
        bib = self._rlm[path][0]
        series = self._rlm[path][1]
        self._rlm[path][col] = new_text
        r = self.rdb.get_rider(bib, series)
        if r is not None:
            if col == 3:
                if new_text != r['org']:
                    r['org'] = new_text
            elif col == 4:
                if new_text.upper() != r['cat']:
                    r['cat'] = new_text.upper()
            elif col == 5:
                if new_text.lower() != r['refid']:
                    r['refid'] = new_text.lower()

    def _view_button_press(self, view, event):
        """Handle mouse button event on tree view"""
        if event.type == Gdk.EventType.BUTTON_PRESS:
            if event.button == Gdk.BUTTON_SECONDARY:
                self._cur_model = view.get_model()
                pathinfo = view.get_path_at_pos(int(event.x), int(event.y))
                if pathinfo is not None:
                    path, col, cellx, celly = pathinfo
                    view.grab_focus()
                    view.set_cursor(path, col, False)
                    sel = view.get_selection().get_selected()
                    if sel is not None:
                        i = sel[1]
                        r = Gtk.TreeModelRow(self._cur_model, i)
                        self._cur_rider_sel = r[7]
                        self._rider_menu_edit.set_sensitive(True)
                        self._rider_menu_del.set_sensitive(True)
                    else:
                        _log.error('Invalid selection ignored')
                        self._cur_rider_sel = None
                        self._rider_menu_edit.set_sensitive(False)
                        self._rider_menu_del.set_sensitive(False)
                else:
                    self._cur_rider_sel = None
                    self._rider_menu_edit.set_sensitive(False)
                    self._rider_menu_del.set_sensitive(False)
                self._rider_menu.popup_at_pointer(None)
                return True
        return False

    def rider_add_cb(self, menuitem, data=None):
        """Create a new rider entry and edit the content"""
        nser = ''
        if self._cur_model is self._clm:
            nser = 'cat'
        dbr = riderdb.rider(series=nser)
        schema = dbr.get_schema()
        rtype = schema['rtype']['prompt']
        short = 'Create New %s' % (rtype)
        res = uiutil.options_dlg(window=self.window,
                                 title=short,
                                 sections={
                                     'rdb': {
                                         'title': 'Rider',
                                         'schema': schema,
                                         'object': dbr,
                                     },
                                 })
        chg = False
        for k in res['rdb']:
            if res['rdb'][k][0]:
                chg = True
                break
        if chg:
            rider = self.rdb.add_rider(dbr, overwrite=False)
            GLib.idle_add(self.select_row, rider)

    def select_row(self, rider):
        """Select rider view model if possible"""
        if rider in self.rdb:
            rdb = self.rdb[rider]
            model = self._rlm
            view = self._rlv
            if rdb['series'].lower() == 'cat':
                model = self._clm
                view = self._clv
            found = False
            for r in model:
                if r[7] == rider:
                    view.set_cursor(r.path, None, False)
                    found = True
                    break
            if not found:
                _log.debug('Entry %r not found, unable to select', rider)
        return False

    def rider_edit_cb(self, menuitem, data=None):
        """Edit properties of currently selected entry in riderdb"""
        if self._cur_rider_sel is not None and self._cur_rider_sel in self.rdb:
            doreopen = False
            rider = self._cur_rider_sel
            dbr = self.rdb[rider]
            schema = dbr.get_schema()
            rtype = schema['rtype']['prompt']
            short = 'Edit %s %s' % (rtype, dbr.get_bibstr())
            res = uiutil.options_dlg(window=self.window,
                                     title=short,
                                     sections={
                                         'rdb': {
                                             'title': 'Rider',
                                             'schema': schema,
                                             'object': dbr,
                                         },
                                     })
            if rtype == 'Team':
                # Patch the org value which is not visible, without notify
                dbr.set_value('org', dbr['no'].upper())
            if res['rdb']['no'][0] or res['rdb']['series'][0]:
                # change of number or series requires some care
                self._cur_rider_sel = None
                newrider = self.rdb.add_rider(dbr,
                                              notify=False,
                                              overwrite=False)
                if rtype == 'Category':
                    if uiutil.questiondlg(
                            window=self.window,
                            question='Update rider categories?',
                            subtext=
                            'Riders in the old category will be updated to the new one',
                            title='Update Cats?'):
                        self.rdb.update_cats(res['rdb']['no'][1],
                                             res['rdb']['no'][2],
                                             notify=False)
                        # and current event
                        if self.curevent is not None:
                            if res['rdb']['no'][1].upper(
                            ) in self.curevent.cats:
                                nc = []
                                for c in self.curevent.cats:
                                    if c == res['rdb']['no'][1].upper():
                                        nc.append(res['rdb']['no'][2].upper())
                                    else:
                                        nc.append(c)
                                self.curevent.loadcats(nc)
                                doreopen = True
                else:
                    # update curevent
                    if self.curevent is not None:
                        if self.curevent.getrider(res['rdb']['no'][1],
                                                  res['rdb']['series'][1]):
                            # rider was in event, add new one
                            self.curevent.addrider(dbr['no'], dbr['series'])
                            if self.curevent.timerstat == 'idle':
                                self.curevent.delrider(res['rdb']['no'][1],
                                                       res['rdb']['series'][1])
                            else:
                                _log.warning(
                                    'Changed rider number %r => %r, check data',
                                    res['rdb']['no'][1], res['rdb']['no'][2])

                # del triggers a global notify
                del (self.rdb[rider])

                # then try to select the modified row
                GLib.idle_add(self.select_row, newrider)

                # then reopen curevent if flagged after notify
                if doreopen:
                    GLib.idle_add(self.event_reload)
            else:
                # notify meet and event of any changes, once
                for k in res['rdb']:
                    if res['rdb'][k][0]:
                        dbr.notify()
                        break

    def rider_lookup_cb(self, menuitem, data=None):
        _log.info('Rider lookup not yet enabled')

    def rider_delete_cb(self, menuitem, data=None):
        """Delete currently selected entry from riderdb"""
        if self._cur_rider_sel is not None and self._cur_rider_sel in self.rdb:
            dbr = self.rdb[self._cur_rider_sel]
            tv = []
            series = dbr['series']
            if series == 'cat':
                tv.append('Category')
                tv.append(dbr['no'].upper())
                tv.append(':')
                tv.append(dbr['first'])
            elif series == 'team':
                tv.append('Team')
                tv.append(dbr['no'].upper())
                tv.append(':')
                tv.append(dbr['first'])
            elif series == 'ds':
                tv.append('DS')
                tv.append(dbr.listname())
            elif series == 'spare':
                tv.append('Spare Bike')
                tv.append(dbr['no'])
                tv.append(dbr['org'])
            else:
                tv.append('Rider')
                tv.append(dbr.get_bibstr())
                tv.append(dbr.listname())
                if dbr['cat']:
                    tv.append(dbr['cat'].upper())
            short = ' '.join(tv[0:2])
            text = 'Delete %s?' % (short)
            info = 'This action will permanently delete %s' % (' '.join(tv))
            if uiutil.questiondlg(window=self.window,
                                  question=text,
                                  subtext=info,
                                  title='Delete?'):
                if self.curevent is not None:
                    if series == 'cat':
                        cat = dbr['no'].upper()
                        if cat in self.curevent.cats:
                            _log.warning('Deleted cat %s in open event', cat)
                    elif series not in ('ds', 'spare', 'team'):
                        self.curevent.delrider(dbr['no'], series)
                        _log.info('Remove rider %s from event', short)
                del (self.rdb[self._cur_rider_sel])
                _log.info('Deleted %s', short)
                self._cur_rider_sel = None
            else:
                _log.debug('Rider delete aborted')

    def __init__(self, etype=None, lockfile=None):
        """Meet constructor."""
        self.loghandler = None  # set in loadconfig to meet dir
        self.exportpath = EXPORTPATH
        if etype not in ROADRACE_TYPES:
            etype = 'road'
        self.etype = etype
        self.meetlock = lockfile
        self.shortname = None
        self.title = ''
        self.host = ''
        self.subtitle = ''
        self.document = ''
        self.date = ''
        self.organiser = ''
        self.pcp = ''
        self.distance = None
        self.diststr = ''
        self.linkbase = '.'
        self.provisionalstart = False
        self.indexlink = None
        self.nextlink = None
        self.prevlink = None

        self.remoteenable = False
        self.lifexport = False
        self.resfiles = True
        self.announceresult = True

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
        self._timer = decoder()
        self.timer = ''
        self._timer.setcb(self._timercb)
        self.timercb = None  # set by event app
        self._alttimer = timy()  # alttimer is always timy
        self.alttimer = ''
        self._alttimer.setcb(self._alttimercb)
        self.alttimercb = None  # set by event app
        self.announce = telegraph()
        self.announce.setcb(self._controlcb)
        self.anntopic = None
        self.mirrorpath = ''
        self.mirrorcmd = None
        self.mirrorfile = ''
        self.mirror = None
        self.eventcode = ''

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
        self.decoder_configure = b.get_object('menu_timing_configure')
        self.event_box = b.get_object('event_box')
        self.stat_but = uiutil.statButton()
        b.get_object('event_stat_but').add(self.stat_but)
        self.action_model = b.get_object('event_action_model')
        self.action_combo = b.get_object('event_action_combo')
        self.action_entry = b.get_object('event_action_entry')
        b.get_object('event_stat_hbox').set_focus_chain(
            [self.action_combo, self.action_entry, self.action_combo])
        self.notebook = b.get_object('meet_nb')

        # prepare local scratch pad ? can these be removed?
        self.an_cur_lap = tod.ZERO
        self.an_cur_split = tod.ZERO
        self.an_cur_bunchid = 0
        self.an_cur_bunchcnt = 0
        self.an_last_time = None
        self.an_cur_start = tod.ZERO

        # setup context menu handles
        self._rider_menu = b.get_object('rider_context')
        self._rider_menu_edit = b.get_object('rider_edit')
        self._rider_menu_lookup = b.get_object('rider_lookup')
        self._rider_menu_del = b.get_object('rider_del')
        self._cur_rider_sel = None
        self._cur_model = None

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
            object,  # rider ref 7
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
        t.connect('button_press_event', self._view_button_press)
        self._rlv = t
        b.get_object('riders_box').add(t)

        # Build a cat list store and view
        self._clm = Gtk.ListStore(
            str,  # ID 0
            str,  # Title 1
            str,  # Subtitle 2
            str,  # Footer 3
            str,  # Target Laps 4
            str,  # Distance 5
            str,  # Start Offset 6
            object,  # Rider ref 7
        )
        t = Gtk.TreeView(self._clm)
        t.set_reorderable(True)
        t.set_rules_hint(True)
        uiutil.mkviewcoltxt(t, 'ID', 0, calign=0.0, width=40)
        uiutil.mkviewcoltxt(t, 'Title', 1, width=140, cb=self._catcol_cb)
        uiutil.mkviewcoltxt(t, 'Subtitle', 2, expand=True, cb=self._catcol_cb)
        uiutil.mkviewcoltxt(t,
                            'Footer',
                            3,
                            width=140,
                            maxwidth=140,
                            cb=self._catcol_cb)
        uiutil.mkviewcoltxt(t,
                            'Laps',
                            4,
                            width=40,
                            calign=1.0,
                            cb=self._catcol_cb)
        uiutil.mkviewcoltxt(t,
                            'Distance',
                            5,
                            width=40,
                            calign=1.0,
                            cb=self._catcol_cb)
        uiutil.mkviewcoltxt(t,
                            'Start Offset',
                            6,
                            width=50,
                            calign=1.0,
                            cb=self._catcol_cb)

        t.show()
        t.connect('button_press_event', self._view_button_press)
        self._clv = t
        b.get_object('cat_box').add(t)

        # get rider db
        _log.debug('Add riderdb')
        self.rdb = riderdb.riderdb()
        self.rdb.set_notify(self._rcb)
        self._tagmap = {}
        self._maptag = {}

        # select event page in notebook.
        self.notebook.set_current_page(0)

        # start timer
        GLib.timeout_add_seconds(1, self.timeout)


class fakemeet(roadmeet):
    """Non-interactive meet wrapper"""

    def __init__(self, rdb):
        self.etype = 'road'
        self.rdb = rdb
        self._timer = decoder()
        self._alttimer = timy()
        self.stat_but = uiutil.statButton()
        self.action_model = Gtk.ListStore(str, str)
        self.action_model.append(['a', 'a'])
        self.action_combo = Gtk.ComboBox()
        self.action_combo.set_model(self.action_model)
        self.action_combo.set_active(0)
        self.announce = telegraph()
        self.title = ''
        self.host = ''
        self.subtitle = ''
        self.date = ''
        self.document = ''
        self.organiser = ''
        self.pcp = ''
        self.distance = None
        self.diststr = ''
        self.linkbase = '.'
        self.provisionalstart = False
        self.indexlink = None
        self.nextlink = None
        self.prevlink = None
        self.eventcode = ''
        self.shortname = ''
        self.mirrorfile = ''

    def cmd_announce(self, command, msg):
        return False

    def rider_announce(self, rvec):
        return False

    def timer_announce(self, evt, timer=None, source=''):
        return False

    def loadconfig(self):
        """Load meet config from disk."""
        cr = jsonconfig.config()
        cr.add_section('roadmeet', _CONFIG_SCHEMA)
        cr.merge(metarace.sysconf, 'roadmeet')
        cr.load(CONFIGFILE)
        cr.export_section('roadmeet', self)


def edit_defaults():
    """Run a sysconf editor dialog"""
    metarace.sysconf.add_section('roadmeet', _CONFIG_SCHEMA)
    metarace.sysconf.add_section('rms', _RMS_SCHEMA)
    metarace.sysconf.add_section('irtt', _IRTT_SCHEMA)
    metarace.sysconf.add_section('trtt', _TRTT_SCHEMA)
    metarace.sysconf.add_section('export', _EXPORT_SCHEMA)
    metarace.sysconf.add_section('telegraph', _TG_SCHEMA)
    metarace.sysconf.add_section('thbc', _THBC_SCHEMA)
    metarace.sysconf.add_section('rru', _RRU_SCHEMA)
    metarace.sysconf.add_section('rrs', _RRS_SCHEMA)
    metarace.sysconf.add_section('timy', _TIMY_SCHEMA)
    cfgres = uiutil.options_dlg(title='Edit Default Configuration',
                                sections={
                                    'roadmeet': {
                                        'title': 'Meet',
                                        'schema': _CONFIG_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'rms': {
                                        'title': 'Road/Cross',
                                        'schema': _RMS_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'irtt': {
                                        'title': 'Individual TT',
                                        'schema': _IRTT_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'trtt': {
                                        'title': 'Teams TT',
                                        'schema': _TRTT_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'export': {
                                        'title': 'Export',
                                        'schema': _EXPORT_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'telegraph': {
                                        'title': 'Telegraph',
                                        'schema': _TG_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'timy': {
                                        'title': 'Timy',
                                        'schema': _TIMY_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'thbc': {
                                        'title': 'THBC',
                                        'schema': _THBC_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'rru': {
                                        'title': 'RR USB',
                                        'schema': _RRU_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                    'rrs': {
                                        'title': 'RR System',
                                        'schema': _RRS_SCHEMA,
                                        'object': metarace.sysconf,
                                    },
                                })

    # check for sysconf changes:
    syschange = False
    for sec in cfgres:
        for key in cfgres[sec]:
            if cfgres[sec][key][0]:
                syschange = True
                break
    if syschange:
        backup = metarace.SYSCONF + '.bak'
        _log.info('Backing up old defaults to %r', backup)
        try:
            if os.path.exists(backup):
                os.unlink(backup)
            os.link(metarace.SYSCONF, backup)
        except Exception as e:
            _log.warning('%s saving defaults backup: %s', e.__class__.__name__,
                         e)
        _log.info('Edit default: Saving sysconf to %r', metarace.SYSCONF)
        with metarace.savefile(metarace.SYSCONF, perm=0o600) as f:
            metarace.sysconf.write(f)
    else:
        _log.info('Edit default: No changes to save')
    return 0


def createmeet():
    """Create a new empty meet folder"""
    ret = None
    count = 0
    dname = 'road_' + tod.datetime.now().date().isoformat()
    cname = dname
    while count < 100:
        mpath = os.path.join(metarace.DATA_PATH, cname)
        if not os.path.exists(mpath):
            os.makedirs(mpath)
            _log.info('Created empty meet folder: %r', mpath)
            ret = mpath
            break
        count += 1
        cname = dname + '_%02d' % (count)
    if ret is None:
        _log.error('Unable to create empty meet folder')
    return ret


def main():
    """Run the road meet application as a console script."""
    chk = Gtk.init_check()
    if not chk[0]:
        print('Unable to init Gtk display')
        sys.exit(-1)

    # attach a console log handler to the root logger
    ch = logging.StreamHandler()
    ch.setLevel(metarace.LOGLEVEL)
    fh = logging.Formatter(metarace.LOGFORMAT)
    ch.setFormatter(fh)
    logging.getLogger().addHandler(ch)

    # try to set the menubar accel and logo
    try:
        lfile = metarace.default_file(metarace.LOGO)
        Gtk.Window.set_default_icon_from_file(lfile)
        mset = Gtk.Settings.get_default()
        mset.set_property('gtk-menu-bar-accel', 'F24')
    except Exception as e:
        _log.debug('%s setting property: %s', e.__class__.__name__, e)

    doconfig = False
    configpath = None
    if len(sys.argv) > 2:
        _log.error('Usage: roadmeet [PATH]')
        sys.exit(1)
    elif len(sys.argv) == 2:
        if sys.argv[1] == '--edit-default':
            doconfig = True
            configpath = metarace.DEFAULTS_PATH
            _log.debug('Edit defaults, configpath: %r', configpath)
        else:
            configpath = sys.argv[1]
    else:
        configpath = createmeet()
    configpath = metarace.config_path(configpath)
    if configpath is None:
        _log.debug('Missing path, command: %r', sys.argv)
        _log.error('Error opening meet')
        if not os.isatty(sys.stdout.fileno()):
            uiutil.messagedlg(
                message='Error opening meet.',
                title='roadmeet: Error',
                subtext='Roadmeet was unable to open a meet folder.')
        sys.exit(-1)

    lf = metarace.lockpath(configpath)
    if lf is None:
        _log.error('Unable to lock meet config, already in use')
        if not os.isatty(sys.stdout.fileno()):
            uiutil.messagedlg(
                message='Meet folder is locked.',
                title='roadmeet: Locked',
                subtext=
                'Another application has locked the meet folder for use.')
        sys.exit(-1)
    _log.debug('Entering meet folder %r', configpath)
    os.chdir(configpath)
    metarace.init()
    if doconfig:
        return edit_defaults()
    else:
        app = roadmeet(None, lf)
        mp = configpath
        if mp.startswith(metarace.DATA_PATH):
            mp = mp.replace(metarace.DATA_PATH + '/', '')
        app.status.push(app.context, 'Meet Folder: ' + mp)
        app.loadconfig()
        app.window.show()
        app.start()
        return Gtk.main()


if __name__ == '__main__':
    sys.exit(main())
