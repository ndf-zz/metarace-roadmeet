# SPDX-License-Identifier: MIT
"""Gtk user interface helper functions"""

import os
import gi
import logging
from importlib.resources import files

gi.require_version("GLib", "2.0")
from gi.repository import GLib

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

gi.require_version('Pango', '1.0')
from gi.repository import Pango

import metarace
from metarace import tod
from metarace import strops
from metarace.jsonconfig import config
from metarace.riderdb import rider

_log = logging.getLogger('uiutil')
_log.setLevel(logging.DEBUG)

# Resources package
RESOURCE_PKG = 'roadmeet.ui'

# Font-overrides
DIGITFONT = Pango.FontDescription('Noto Mono Medium 22')
MONOFONT = Pango.FontDescription('Noto Mono')
LOGVIEWFONT = Pango.FontDescription('Noto Mono 11')

# Cell renderer styles
STYLE_ITALIC = Pango.Style.ITALIC
STYLE_NORMAL = Pango.Style.NORMAL

# Timer text
FIELDWIDTH = '00h00:00.0000'
ARMTEXT = '       0.0   '

# Button indications
_button_images = {
    'idle': {
        'src': 'bg_idle.svg'
    },
    'activity': {
        'src': 'bg_armint.svg'
    },
    'ok': {
        'src': 'bg_armstart.svg'
    },
    'error': {
        'src': 'bg_armfin.svg'
    },
}


def _load_images(store):
    """Create image handles for the status button"""
    for b in store:
        img = store[b]
        with metarace.resource_file(img['src']) as fn:
            img['image'] = Gtk.Image.new_from_file(str(fn))


class statButton(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        if 'image' not in _button_images['idle']:
            _load_images(_button_images)
        srcbuf = _button_images['idle']['image'].get_pixbuf()
        self.__curbg = 'idle'
        self.__image = Gtk.Image.new_from_pixbuf(srcbuf)
        self.__image.show()
        self.__label = Gtk.Label.new(u'--')
        self.__label.set_width_chars(12)
        self.__label.set_single_line_mode(True)
        self.__label.show()
        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_spacing(2)
        self.pack_start(self.__image, False, True, 0)
        self.pack_start(self.__label, True, True, 0)
        self.show()
        self.set_sensitive(False)
        self.set_can_focus(False)

    def update(self, bg=None, label=None):
        """Update button content"""
        if bg is not None and bg != self.__curbg and bg in _button_images:
            srcbuf = _button_images[bg]['image'].get_pixbuf()
            self.__image.set_from_pixbuf(srcbuf)
            self.__curbg = bg
        if label is not None:
            self.__label.set_text(label)


class textViewHandler(logging.Handler):
    """A class for displaying log messages in a GTK text view."""

    def __init__(self, log=None, view=None, scroll=None):
        self.log = log
        self.view = view
        self.scroll = scroll
        self.scroll_pending = False
        logging.Handler.__init__(self)

    def do_scroll(self):
        """Catch up end of scrolled window."""
        if self.scroll_pending:
            self.scroll.set_value(self.scroll.get_upper() -
                                  self.scroll.get_page_size())
            self.scroll_pending = False
        return False

    def append_log(self, msg):
        """Append msg to the text view."""
        atend = True
        if self.scroll and self.scroll.get_page_size() > 0:
            # Fudge a 'sticky' end of scroll mode... about a pagesz
            pagesz = self.scroll.get_page_size()
            if self.scroll.get_upper() - (self.scroll.get_value() + pagesz) > (
                    0.5 * pagesz):
                atend = False
        self.log.insert(self.log.get_end_iter(), msg.strip() + '\n')
        if atend:
            self.scroll_pending = True
            GLib.idle_add(self.do_scroll)
        return False

    def emit(self, record):
        """Emit log record to gtk main loop."""
        msg = self.format(record)
        GLib.idle_add(self.append_log, msg)


class statusHandler(logging.Handler):
    """A class for displaying log messages in a GTK status bar."""

    def __init__(self, status=None, context=0):
        self.status = status
        self.context = context
        logging.Handler.__init__(self)

    def pull_status(self, msgid):
        """Remove specified msgid from the status stack."""
        self.status.remove(self.context, msgid)
        return False

    def push_status(self, msg, level):
        """Push the given msg onto the status stack, and defer removal."""
        delay = 3
        if level > 25:
            delay = 8
        msgid = self.status.push(self.context, msg)
        GLib.timeout_add_seconds(delay, self.pull_status, msgid)
        return False

    def emit(self, record):
        """Emit log record to gtk main loop."""
        msg = self.format(record)
        GLib.idle_add(self.push_status, msg, record.levelno)


class timerpane:

    def setrider(self, bib=None, ser=None):
        """Set bib for timer."""
        if bib is not None:
            self.bibent.set_text(bib)
            if ser is not None:
                self.serent.set_text(ser)
            self.bibent.activate()  # and chain events

    def grab_focus(self, data=None):
        """Steal focus into bib entry."""
        self.bibent.grab_focus()
        return False  # allow addition to idle_add or delay

    def getrider(self):
        """Return bib loaded into timer."""
        return self.bibent.get_text()

    def getstatus(self):
        """Return timer status.

        Timer status may be one of:

          'idle'        -- lane empty or ready for new rider
          'load'        -- rider loaded into lane
          'armstart'    -- armed for start trigger
          'running'     -- timer running
          'armint'      -- armed for intermediate split
          'armfin'      -- armed for finish trigger
          'finish'      -- timer finished

        """
        return self.status

    def set_time(self, tstr=''):
        """Set timer string."""
        self.ck.set_text(tstr)

    def get_time(self):
        """Return current timer string."""
        return self.ck.get_text()

    def show_splits(self):
        """Show the split button and label."""
        self.ls.show()
        self.lb.show()

    def hide_splits(self):
        """Hide the split button and label."""
        self.ls.hide()
        self.lb.hide()

    def set_split(self, split=None):
        """Set the split pointer and update label."""
        # update split index from supplied argument
        if isinstance(split, int):
            if split >= 0 and split < len(self.splitlbls):
                self.split = split
            else:
                _log.warning('Requested split %r not in range %r', split,
                             self.splitlbls)
        elif isinstance(split, str):
            if split in self.splitlbls:
                self.split = self.splitlbls.index(split)
            else:
                _log.warning('Requested split %r not found %r', split,
                             self.splitlbls)
        else:
            self.split = -1  # disable label

        # update label to match current split
        if self.split >= 0 and self.split < len(self.splitlbls):
            self.ls.set_text(self.splitlbls[self.split])
        else:
            self.ls.set_text('')

    def on_halflap(self):
        """Return true is current split pointer is a half-lap."""
        return self.split % 2 == 0

    def lap_up(self):
        """Increment the split point to the next whole lap."""
        nsplit = self.split
        if self.on_halflap():
            nsplit += 1
        else:
            nsplit += 2
        self.set_split(nsplit)

    def lap_up_clicked_cb(self, button, data=None):
        """Respond to lap up button press."""
        if self.status in ['running', 'armint', 'armfin']:
            self.missedlap()

    def runtime(self, runtod):
        """Update timer run time."""
        if runtod > self.recovtod:
            self.set_time(runtod.timestr(1))

    def missedlap(self):
        """Flag a missed lap to allow 'catchup'."""
        _log.info('No time recorded for split %r', self.split)
        self.lap_up()

    def get_sid(self, inter=None):
        """Return the split id for the supplied, or current split."""
        if inter is None:
            inter = self.split
        ret = None
        if inter >= 0 and inter < len(self.splitlbls):
            ret = self.splitlbls[inter]
        return ret

    def intermed(self, inttod, recov=4):
        """Trigger an intermediate time."""
        nt = inttod - self.starttod
        if self.on_halflap():
            # reduce recover time on half laps
            recov = 2
        self.recovtod.timeval = nt.timeval + recov
        self.set_time(nt.timestr(3))
        self.torunning()

        # store intermedate split in local split cache
        sid = self.get_sid()
        self.splits[sid] = inttod

    def difftime(self, dt):
        """Overwrite split time with a difference time."""
        dstr = ('+' + dt.rawtime(2) + ' ').rjust(12)
        self.set_time(dstr)

    def getsplit(self, inter):
        """Return split for specified passing."""
        ret = None
        sid = self.get_sid(inter)
        if sid in self.splits:
            ret = self.splits[sid]
        return ret

    def finish(self, fintod):
        """Trigger finish on timer."""
        # Note: split pointer is not updated, so after finish, if
        #       labels are loaded, the current split will point to
        #       a dummy sid for event distance
        self.finishtod = fintod
        self.ls.set_text('Finish')
        self.set_time((self.finishtod - self.starttod).timestr(3))
        self.tofinish()

    def tofinish(self, status='finish'):
        """Set timer to finished."""
        self.status = status
        self.b.update('idle', 'Finished')
        self.b.set_sensitive(False)

    def toarmfin(self):
        """Arm timer for finish."""
        self.status = 'armfin'
        self.b.update('error', 'Finish Armed')
        self.b.set_sensitive(True)

    def toarmint(self, label='Lap Armed'):
        """Arm timer for intermediate."""
        self.status = 'armint'
        self.b.update('activity', label)
        self.b.set_sensitive(True)

    def torunning(self):
        """Update timer state to running."""
        self.bibent.set_sensitive(False)
        self.serent.set_sensitive(False)
        self.status = 'running'
        self.b.update('ok', 'Running')
        self.b.set_sensitive(True)

    def start(self, starttod):
        """Trigger start on timer."""
        self.starttod = starttod
        self.set_split(0)
        self.torunning()

    def toload(self, bib=None):
        """Load timer."""
        self.status = 'load'
        self.starttod = None
        self.recovtod = tod.tod(0)  # timeval is manipulated
        self.finishtod = None
        self.set_time()
        self.splits = {}
        self.set_split()
        if bib is not None:
            self.setrider(bib)
        self.b.update('idle', 'Ready')
        self.b.set_sensitive(True)

    def toarmstart(self):
        """Set state to armstart."""
        self.status = 'armstart'
        self.set_split()
        self.set_time(ARMTEXT)
        self.b.update('activity', 'Start Armed')
        self.b.set_sensitive(True)

    def disable(self):
        """Disable rider bib entry field."""
        self.bibent.set_sensitive(False)
        self.serent.set_sensitive(False)

    def enable(self):
        """Enable rider bib entry field."""
        self.bibent.set_sensitive(True)
        self.serent.set_sensitive(True)

    def toidle(self):
        """Set timer state to idle."""
        self.status = 'idle'
        self.bib = None
        self.bibent.set_text('')
        self.bibent.set_sensitive(True)
        self.serent.set_sensitive(True)
        self.biblbl.set_text('')
        self.starttod = None
        self.recovtod = tod.tod(0)
        self.finishtod = None
        self.split = -1  # next expected passing
        self.splits = {}  # map of split ids to split data
        self.set_split()
        self.set_time()
        self.b.update('idle', 'Idle')
        self.b.set_sensitive(False)

    def __init__(self, label='Timer', doser=False):
        """Constructor."""
        _log.debug('Building timerpane: %r', label)
        s = Gtk.Frame.new(label)
        s.set_border_width(5)
        s.set_shadow_type(Gtk.ShadowType.IN)
        s.show()
        self.doser = doser

        #v = Gtk.VBox(False, 5)
        v = Gtk.Box.new(Gtk.Orientation.VERTICAL, 5)
        v.set_homogeneous(False)
        v.set_border_width(5)

        # Bib and name label
        #h = Gtk.HBox(False, 5)
        h = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 5)
        h.set_homogeneous(False)
        l = Gtk.Label.new('Rider #:')
        l.show()
        h.pack_start(l, False, True, 0)
        self.bibent = Gtk.Entry.new()
        self.bibent.set_width_chars(3)
        self.bibent.show()
        h.pack_start(self.bibent, False, True, 0)
        self.serent = Gtk.Entry.new()
        self.serent.set_width_chars(2)
        if self.doser:
            self.serent.show()
        h.pack_start(self.serent, False, True, 0)
        self.biblbl = Gtk.Label.new('')
        self.biblbl.show()
        h.pack_start(self.biblbl, True, True, 0)

        # mantimer entry
        self.tment = Gtk.Entry.new()
        self.tment.set_width_chars(10)
        h.pack_start(self.tment, False, True, 0)
        #h.set_focus_chain([self.bibent, self.tment, self.bibent])
        h.show()

        v.pack_start(h, False, True, 0)

        # Clock row 'HHhMM:SS.DCMZ'
        self.ck = Gtk.Label.new(FIELDWIDTH)
        self.ck.set_alignment(0.5, 0.5)
        self.ck.modify_font(DIGITFONT)
        self.ck.show()
        v.pack_start(self.ck, True, True, 0)

        # Timer ctrl/status button
        #h = Gtk.HBox(False, 5)
        h = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 5)
        h.set_homogeneous(False)

        b = Gtk.Button.new()
        b.show()
        b.set_property('can-focus', False)
        self.b = statButton()
        b.add(self.b)
        self.b.update('idle', 'Idle')
        h.pack_start(b, True, True, 0)
        self.ls = Gtk.Label.new('')
        h.pack_start(self.ls, False, True, 0)
        self.lb = Gtk.Button.new_with_label('+')
        self.lb.set_border_width(5)
        self.lb.set_property('can-focus', False)
        self.lb.connect('clicked', self.lap_up_clicked_cb)
        h.pack_start(self.lb, False, True, 0)
        h.show()
        v.pack_start(h, False, True, 0)
        v.show()
        s.add(v)
        self.frame = s
        self.splitlbls = []  # ordered set of split ids
        self.toidle()


def builder(resource=None):
    """Create and return a Gtk.Builder loaded from named resource"""
    ret = None
    _log.debug('Creating Gtk Builder for resource %r', resource)
    rf = files(RESOURCE_PKG).joinpath(resource)
    if rf is not None and rf.is_file():
        ret = Gtk.Builder()
        ret.add_from_string(rf.read_text(encoding='utf-8'))
    else:
        _log.error('Unable to read resource %r: %r', resource, rf)
    return ret


def about_dlg(window, version=None):
    """Display shared about dialog."""
    modal = window is not None
    dlg = Gtk.AboutDialog(modal=modal, destroy_with_parent=True)
    dlg.set_transient_for(window)
    dlg.set_program_name('roadmeet')
    vtxt = 'Library: ' + metarace.VERSION
    if version:
        vtxt = 'Application: ' + version + '; ' + vtxt
    dlg.set_version(vtxt)
    dlg.set_copyright(
        'Copyright \u00a9 2012-2023 Nathan Fraser and contributors')
    dlg.set_comments('Road cycle race result handler')
    dlg.set_license_type(Gtk.License.MIT_X11)
    dlg.set_license(metarace.LICENSETEXT)
    dlg.set_wrap_license(True)
    dlg.run()
    dlg.hide()
    dlg.destroy()


def chooseCsvFile(title='',
                  mode=Gtk.FileChooserAction.OPEN,
                  parent=None,
                  path=None,
                  hintfile=None):
    ret = None
    modal = parent is not None
    dlg = Gtk.FileChooserNative(title=title, modal=modal)
    dlg.set_transient_for(parent)
    dlg.set_action(mode)
    cfilt = Gtk.FileFilter()
    cfilt.set_name('CSV Files')
    cfilt.add_mime_type('text/csv')
    cfilt.add_pattern('*.csv')
    dlg.add_filter(cfilt)
    cfilt = Gtk.FileFilter()
    cfilt.set_name('All Files')
    cfilt.add_pattern('*')
    dlg.add_filter(cfilt)
    if path is not None:
        dlg.set_current_folder(path)
    if hintfile:
        dlg.set_current_name(hintfile)
    response = dlg.run()
    if response == Gtk.ResponseType.ACCEPT:
        ret = dlg.get_filename()
    dlg.destroy()
    return ret


def mkviewcoltod(view=None,
                 header='',
                 cb=None,
                 width=120,
                 editcb=None,
                 colno=None):
    """Return a Time of Day view column."""
    i = Gtk.CellRendererText()
    i.set_property('xalign', 1.0)
    j = Gtk.TreeViewColumn(header, i)
    j.set_cell_data_func(i, cb, colno)
    if editcb is not None:
        i.set_property('editable', True)
        i.connect('edited', editcb, colno)
    j.set_min_width(width)
    view.append_column(j)
    return j


def mkviewcoltxt(view=None,
                 header='',
                 colno=None,
                 cb=None,
                 width=None,
                 halign=None,
                 calign=None,
                 expand=False,
                 editcb=None,
                 maxwidth=None,
                 minwidth=None,
                 charwidth=None,
                 bgcol=None,
                 fontdesc=None,
                 wrap=None,
                 fixed=False,
                 valign=None):
    """Return a text view column."""
    i = Gtk.CellRendererText()
    if cb is not None:
        i.set_property('editable', True)
        i.connect('edited', cb, colno)
    if calign is not None:
        i.set_property('xalign', calign)
    if valign is not None:
        i.set_property('yalign', valign)
    if fontdesc is not None:
        i.set_property('font_desc', fontdesc)
    if charwidth is not None:
        i.set_property('width_chars', charwidth)
    if wrap is not None:
        if minwidth is None:
            minwidth = 400
        if wrap:
            i.set_property('wrap-mode', Pango.WrapMode.WORD_CHAR)
            i.set_property('wrap-width', minwidth)
        else:
            i.set_property('wrap-width', -1)

    j = Gtk.TreeViewColumn(header, i, text=colno)
    if bgcol is not None:
        j.add_attribute(i, 'background', bgcol)
    if halign is not None:
        j.set_alignment(halign)
    if fixed:
        j.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
    if expand:
        if width is not None:
            j.set_min_width(width)
        j.set_expand(True)
    else:
        if width is not None:
            j.set_min_width(width)
    if maxwidth is not None and wrap is None:
        j.set_max_width(maxwidth)
    view.append_column(j)
    if editcb is not None:
        i.connect('editing-started', editcb)
    return i


def mkviewcolbg(view=None,
                header='',
                colno=None,
                cb=None,
                width=None,
                halign=None,
                calign=None,
                expand=False,
                editcb=None,
                maxwidth=None):
    """Return a text view column."""
    i = Gtk.CellRendererText()
    if cb is not None:
        i.set_property('editable', True)
        i.connect('edited', cb, colno)
    if calign is not None:
        i.set_property('xalign', calign)
    j = Gtk.TreeViewColumn(header, i, background=colno)
    if halign is not None:
        j.set_alignment(halign)
    if expand:
        if width is not None:
            j.set_min_width(width)
        j.set_expand(True)
    else:
        if width is not None:
            j.set_min_width(width)
    if maxwidth is not None:
        j.set_max_width(maxwidth)
    view.append_column(j)
    if editcb is not None:
        i.connect('editing-started', editcb)
    return i


def mkviewcolbool(view=None,
                  header='',
                  colno=None,
                  cb=None,
                  width=None,
                  expand=False):
    """Return a boolean view column."""
    i = Gtk.CellRendererToggle()
    i.set_property('activatable', True)
    if cb is not None:
        i.connect('toggled', cb, colno)
    j = Gtk.TreeViewColumn(header, i, active=colno)
    if expand:
        j.set_min_width(width)
        j.set_expand(True)
    else:
        if width is not None:
            j.set_min_width(width)
    view.append_column(j)
    return i


def coltxtbibser(col, cr, model, iter, data):
    """Display a bib.ser string in a tree view."""
    (bibcol, sercol) = data
    cr.set_property(
        'text',
        strops.bibser2bibstr(model.get_value(iter, bibcol),
                             model.get_value(iter, sercol)))


def mkviewcolbibser(view=None,
                    header='No.',
                    bibcol=0,
                    sercol=1,
                    width=None,
                    expand=False):
    """Return a column to display bib/series as a bib.ser string."""
    i = Gtk.CellRendererText()
    i.set_property('xalign', 1.0)
    j = Gtk.TreeViewColumn(header, i)
    j.set_cell_data_func(i, coltxtbibser, (bibcol, sercol))
    if expand:
        j.set_min_width(width)
        j.set_expand(True)
    else:
        if width is not None:
            j.set_min_width(width)
    view.append_column(j)
    return i


def messagedlg(window=None,
               message='Message',
               message_type=Gtk.MessageType.ERROR,
               buttons=Gtk.ButtonsType.CLOSE,
               subtext=None,
               title=None):
    """Display a message dialog."""
    modal = window is not None
    dlg = Gtk.MessageDialog(modal=modal,
                            message_type=message_type,
                            buttons=buttons,
                            text=message,
                            destroy_with_parent=True)
    dlg.set_transient_for(window)
    if title:
        dlg.set_title(title)
    if subtext is not None:
        dlg.format_secondary_text(subtext)
    ret = False
    response = dlg.run()
    _log.debug('Message dialog %r returned: %r', title, response)
    dlg.hide()
    if response == Gtk.ResponseType.OK:
        ret = True
    dlg.destroy()
    return ret


def questiondlg(window=None, question='Question?', subtext=None, title=None):
    """Display a question dialog and return True/False."""
    return messagedlg(window=window,
                      message=question,
                      message_type=Gtk.MessageType.QUESTION,
                      buttons=Gtk.ButtonsType.OK_CANCEL,
                      subtext=subtext,
                      title=title)


class option:
    """Base class for configuration option"""

    def __init__(self, key, schema, obj=None, section=None):
        self.key = key
        self._obj = obj
        self._section = section
        self._attr = None
        self._value = None
        self._oldvalue = None
        self._default = None
        self._type = 'str'
        self._prompt = None
        self._hint = None
        self._subtext = None
        self._control = None
        self._options = {}
        self._places = 0
        self._nowbut = False
        self._readonly = False
        self._defer = False

        # import schema
        if 'type' in schema:
            self._type = schema['type']
        if obj is not None:
            if isinstance(self._obj, config):
                self._attr = key
            elif 'attr' in schema:
                if isinstance(self._obj, (rider, dict)):
                    self._attr = schema['attr']
                else:
                    if schema['attr'] is not None and hasattr(
                            self._obj, schema['attr']):
                        self._attr = schema['attr']
        if 'default' in schema:
            self._default = schema['default']
        if 'value' in schema:
            self._value = schema['value']
        if self._attr is not None and self._value is None:
            if isinstance(self._obj, rider):
                self._value = self._obj[self._attr]
            elif isinstance(self._obj, config):
                self._value = self._obj.get_value(self._section, self._attr)
            elif isinstance(self._obj, dict):
                if self._attr in self._obj:
                    valid, value = self.parse_value(self._obj[self._attr])
                    if valid:
                        self._value = value
            else:
                self._value = getattr(self._obj, self._attr)
        self._oldvalue = self._value
        if 'prompt' in schema:
            self._prompt = schema['prompt']
        else:
            self._prompt = key
        if 'hint' in schema:
            self._hint = schema['hint']
        if 'subtext' in schema:
            self._subtext = schema['subtext']
        if 'places' in schema:
            self._places = strops.confopt_posint(schema['places'], 0)
        if 'readonly' in schema:
            self._readonly = bool(schema['readonly'])
        if 'nowbut' in schema:
            self._nowbut = bool(schema['nowbut'])
        if 'defer' in schema:
            self._defer = bool(schema['defer'])
        if 'options' in schema:
            if isinstance(schema['options'], dict):
                self._options[''] = '[Not set]'
                for kv in schema['options']:
                    k = str(kv)
                    v = schema['options'][kv]
                    self._options[k] = v

    def changed(self):
        """Return true if current value differ from original"""
        return self._value != self._oldvalue

    def get_prev(self):
        """Return original option value"""
        return self._oldvalue

    def get_value(self):
        """Return the option's current value"""
        return self._value

    def reset(self):
        self.set_value(self._oldvalue)

    def validate(self):
        """Check proposed value in control"""
        return self.read_value(self._control.get_text())

    def parse_value(self, newtext):
        """Return a value of the correct type for this option"""
        ret = False
        newval = None
        if self._type == 'tod':
            if newtext:
                newval = tod.mktod(newtext)
                if newval is not None:
                    ret = True
            else:
                ret = True
        elif self._type == 'int':
            if newtext is not None and newtext != '':
                newval = strops.confopt_int(newtext)
                if newval is not None:
                    ret = True
            else:
                ret = True
        elif self._type == 'bool':
            newval = strops.confopt_bool(newtext)
            ret = True
        elif self._type == 'chan':
            if newtext:
                newval = strops.confopt_chan(newtext)
                if newval != strops.CHAN_UNKNOWN:
                    ret = True
            else:
                ret = True
        elif self._type == 'float':
            if newtext:
                newval = strops.confopt_float(newtext)
                if newval is not None:
                    ret = True
            else:
                ret = True
        elif self._type == 'str':
            if newtext is not None:
                newval = str(newtext)
                if not newval:
                    # Allow unset of values without default
                    if self._default is None:
                        newval = None
            ret = True
        else:
            _log.warning('Unknown option value type %r=%r', self._type,
                         newtext)
            newval = newtext
            ret = True
        return ret, newval

    def read_value(self, newtext):
        """Try to read and update value from newtext"""
        ret, newval = self.parse_value(newtext)
        if ret:
            self.set_value(newval)
        return ret

    def format_value(self):
        """Return a string for use in a text entry"""
        ret = ''
        if self._value is not None:
            if self._type == 'tod':
                if self._value is not None:
                    ret = self._value.rawtime(places=self._places)
            else:
                ret = str(self._value)
        return ret

    def set_value(self, newval):
        """Store new value in object and update obj if provided"""
        self._value = newval
        if self._obj is not None and self._attr is not None:
            if isinstance(self._obj, rider):
                # Don't trigger notify in this path - leave that to the caller
                self._obj.set_value(self._attr, self._value)
            elif isinstance(self._obj, config):
                self._obj.set(self._section, self._attr, self._value)
            elif isinstance(self._obj, dict):
                self._obj[self._attr] = self._value
            else:
                # assume object.attribute
                setattr(self._obj, self._attr, self._value)
        if self.changed():
            _log.debug('Option %r update value: %r=>%r (%s)', self.key,
                       self._oldvalue, self._value,
                       self._value.__class__.__name__)
        return True

    def _updated(self, control):
        """Handle update event on control"""
        if self.read_value(control.get_text()):
            control.set_text(self.format_value())
            return True
        else:
            return False

    def _prompt_label(self):
        """Return a prompt label"""
        lbl = Gtk.Label(label=self._prompt)
        lbl.set_single_line_mode(True)
        lbl.set_halign(Gtk.Align.START)
        lbl.show()
        return lbl

    def add_control(self, grid, row):
        """Create a new control and add it to the provided grid"""
        grid.attach(self._prompt_label(), 0, row, 1, 1)

        self._control = Gtk.Entry()
        self._control.set_width_chars(32)
        self._control.set_activates_default(True)
        if self._value is not None:
            self._control.set_text(self.format_value())
        if self._hint is not None:
            self._control.set_tooltip_text(self._hint)
        self._control.show()
        if not self._defer:
            self._control.connect('activate', self._updated)
        if self._readonly:
            self._control.set_editable(False)
        grid.attach(self._control, 1, row, 4, 1)
        return 1


class optionShort(option):

    def _now_press(self, widget, event):
        """Transfer now into value and re-validate"""
        self._value = tod.now().truncate(self._places)
        self._control.set_text(self.format_value())

    def add_control(self, grid, row):
        """Create a new control and add it to the provided grid"""
        grid.attach(self._prompt_label(), 0, row, 1, 1)

        self._control = Gtk.Entry()
        self._control.set_width_chars(12)
        self._control.set_activates_default(True)
        if self._value is not None:
            self._control.set_text(self.format_value())
        if self._hint is not None:
            self._control.set_tooltip_text(self._hint)
        self._control.show()
        if not self._defer:
            self._control.connect('activate', self._updated)
        if self._readonly:
            self._control.set_editable(False)
        grid.attach(self._control, 1, row, 1, 1)

        if self._nowbut:
            but = Gtk.Button()
            but.set_label('Now')
            but.set_halign(Gtk.Align.START)
            but.set_can_focus(False)
            but.show()
            if self._subtext:
                but.set_tooltip_text(self._subtext)
            else:
                but.set_tooltip_text('Set the tod entry to now')
            but.connect('button-press-event', self._now_press)
            grid.attach(but, 2, row, 1, 1)
        else:
            subtext = ''
            if self._subtext:
                subtext = self._subtext
            lbl = Gtk.Label(label=subtext)
            lbl.set_single_line_mode(True)
            lbl.set_halign(Gtk.Align.START)
            lbl.show()
            grid.attach(lbl, 2, row, 3, 1)
        return 1


class optionCheck(option):

    def validate(self):
        """Check proposed value in control"""
        return self.read_value(self._control.get_active())

    def _updated(self, control):
        """Handle update event on control"""
        return self.read_value(self._control.get_active())

    def add_control(self, grid, row):
        """Create a new control and add it to the provided grid"""
        grid.attach(self._prompt_label(), 0, row, 1, 1)

        st = ''
        if self._subtext:
            st = self._subtext
        self._control = Gtk.CheckButton.new_with_label(st)
        if self._value:
            self._control.set_active(True)
        if self._hint is not None:
            self._control.set_tooltip_text(self._hint)
        self._control.show()
        if not self._defer:
            self._control.connect('toggled', self._updated)
        if self._readonly:
            self._control.set_sensitive(False)
        grid.attach(self._control, 1, row, 3, 1)
        return 1


class optionHidden(option):
    """Hidden option with value and type"""

    def add_control(self, grid, row):
        return 0

    def validate(self):
        return True


class optionLabel(option):

    def add_control(self, grid, row):
        grid.attach(self._prompt_label(), 0, row, 1, 1)

        self._control = Gtk.Label()
        self._control.set_halign(Gtk.Align.START)
        self._control.set_selectable(True)
        self._control.set_margin_top(4)
        self._control.set_margin_bottom(4)
        if self._value is not None:
            self._control.set_text(self.format_value())
        if self._hint is not None:
            self._control.set_tooltip_text(self._hint)
        self._control.show()
        grid.attach(self._control, 1, row, 4, 1)
        return 1

    def validate(self):
        return True


class optionChoice(option):

    def validate(self):
        """Check proposed value in control"""
        newval = self._control.get_active_id()
        if newval == '':
            # Allow un-set of option value
            newval = None
        return self.read_value(newval)

    def _updated(self, control):
        """Handle update event on control"""
        return self.validate()

    def add_control(self, grid, row):
        """Create a new control and add it to the provided grid"""
        grid.attach(self._prompt_label(), 0, row, 1, 1)

        self._control = Gtk.ComboBoxText.new()
        for k in self._options:
            self._control.append(k, self._options[k])
        if self._value is not None:
            self._control.set_active_id(self.format_value())
        else:
            self._control.set_active_id('')
        self._control.show()
        if not self._defer:
            self._control.connect('changed', self._updated)
        if self._readonly:
            self._control.set_sensitive(False)
        if self._hint is not None:
            self._control.set_tooltip_text(self._hint)
        grid.attach(self._control, 1, row, 3, 1)

        subtext = ''
        if self._subtext:
            subtext = self._subtext
        lbl = Gtk.Label(label=subtext)
        lbl.set_single_line_mode(True)
        lbl.show()
        grid.attach(lbl, 4, row, 1, 1)

        return 1


class optionSection(option):

    def validate(self):
        return True

    def add_control(self, grid, row):
        """Create a new control and add it to the provided grid"""
        lbl = Gtk.Label(label=self._prompt)
        lbl.set_single_line_mode(True)
        lbl.set_halign(Gtk.Align.START)
        if hasattr(Pango.AttrList, 'from_string'):
            lbl.set_attributes(Pango.AttrList.from_string('0 -1 style italic'))
        if row != 0:
            lbl.set_margin_top(15)
        lbl.show()
        grid.attach(lbl, 0, row, 5, 1)
        return 1


def options_dlg(window=None, title='Options', sections={}):
    """Build and display an option editor for the provided sections

      sections={
        "section": {
          "object": OBJECT, rider or section
          "title": section label
          "schema": {
            "key": {
              "value": [Original value],
              "control": [Control type],
              "type" : [Value type],
              "prompt": [Prompt text],
              "subtext": [Extra info],
              "hint": [Tooltip],
              "places": [Decimal places for tod value],
              "attr": [Attribute in optional obj for direct edit],
              "options": { "key":"Text", ... },
            },
            ...
          }

       Value types:

         str: text string
         int: integer value
         float: floating point number
         chan: timing channel
         bool: True/False
         tod: Time of day object with number of places in schema

       Control types:

         none: nothing displayed for the config option
         label: prompt and value as readonly text
         section: section delimiter
         text: standard text input
         short: short text input, extra info displayed right of input
         check: on/off selection, extra info displayed right of input
         choice: select box, choice of options provided in schema

    Return value is a dict of dicts with one tuple per key:

        "section": {"key": (changed, oldval, newval), ...}, ...

    Note: section controls return (False, None, None)
    """
    omap = {}
    # read schema into options map
    for sec in sections:
        omap[sec] = {'title': sections[sec]['title'], 'options': {}}
        for key in sections[sec]['schema']:
            oschema = sections[sec]['schema'][key]
            obj = sections[sec]['object']
            otype = 'text'
            if 'control' in oschema:
                otype = oschema['control']
            if otype == 'section':
                omap[sec]['options'][key] = optionSection(
                    key, oschema, obj, sec)
            elif otype == 'short':
                omap[sec]['options'][key] = optionShort(key, oschema, obj, sec)
            elif otype == 'check':
                omap[sec]['options'][key] = optionCheck(key, oschema, obj, sec)
            elif otype == 'label':
                omap[sec]['options'][key] = optionLabel(key, oschema, obj, sec)
            elif otype == 'choice':
                omap[sec]['options'][key] = optionChoice(
                    key, oschema, obj, sec)
            elif otype == 'none':
                omap[sec]['options'][key] = optionHidden(
                    key, oschema, obj, sec)
            else:
                omap[sec]['options'][key] = option(key, oschema, obj, sec)

    # build dialog
    modal = window is not None
    dlg = Gtk.Dialog(title=title, modal=modal, destroy_with_parent=True)
    dlg.set_transient_for(window)
    dlg.add_buttons("Cancel", 2, "OK", 0)
    dlg.set_default_response(0)

    # container type depends on number of config sections
    ctr = None
    onePage = False
    if len(omap) == 1:
        ctr = Gtk.ScrolledWindow()
        ctr.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ctr.set_propagate_natural_height(True)
        onePage = True
    else:
        ctr = Gtk.Notebook()
        ctr.set_tab_pos(Gtk.PositionType.LEFT)
    ctr.show()
    dlg.get_content_area().pack_start(ctr, True, True, 0)

    for section in omap:
        grid = Gtk.Grid()
        grid.props.margin = 8
        grid.set_row_spacing(4)
        grid.set_column_spacing(8)
        grid.set_column_homogeneous(False)
        grid.set_row_homogeneous(False)
        row = 0
        for key in omap[section]['options']:
            rows = omap[section]['options'][key].add_control(grid, row)
            row += rows
        grid.show()
        if onePage:
            ctr.add(grid)
        else:
            scr = Gtk.ScrolledWindow()
            scr.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scr.set_propagate_natural_height(True)
            scr.add(grid)
            scr.show()
            l = Gtk.Label()
            l.set_text(omap[section]['title'])
            l.set_width_chars(12)
            l.show()
            ctr.append_page(scr, l)

    retval = dlg.run()
    dlg.hide()

    # change report
    res = {}
    if retval == 2:
        # on cancel, reset all values and report no changes
        for section in omap:
            res[section] = {}
            sec = omap[section]['options']
            for key in sec:
                o = sec[key]
                o.reset()
                res[section][key] = (False, o.get_prev(), o.get_prev())
    else:
        # re-validate all entries and report changes
        for section in omap:
            res[section] = {}
            sec = omap[section]['options']
            for key in sec:
                o = sec[key]
                if not o.validate():
                    _log.warning('Invalid value for option %r ignored', key)
                res[section][key] = (o.changed(), o.get_prev(), o.get_value())

    dlg.destroy()
    return res


class decisionEditor:

    def __init__(self, window=None, decisions=[]):
        modal = window is not None
        self._dlg = Gtk.Dialog(
            title="Edit Decisions of the Commissaires Panel",
            modal=modal,
            destroy_with_parent=True)
        self._dlg.set_transient_for(window)
        self._dlg.add_buttons("Done", 0)

        self._model = Gtk.ListStore(str, str)
        for d in decisions:
            self._model.append((
                '\u2023',
                d.strip(),
            ))
        self._view = Gtk.TreeView(self._model)
        self._view.set_reorderable(True)
        self._view.set_rules_hint(False)
        self._view.set_headers_visible(False)
        self._view.set_property('height-request', 200)
        mkviewcoltxt(self._view, 'Bullet', 0, charwidth=2, valign=0.0)
        mkviewcoltxt(self._view,
                     'Decision',
                     1,
                     expand=True,
                     width=400,
                     charwidth=66,
                     cb=self.edit_decision,
                     wrap=True,
                     valign=0.0)
        self._view.show()
        ctr = Gtk.ScrolledWindow()
        ctr.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ctr.set_propagate_natural_height(True)
        ctr.add(self._view)
        ctr.show()
        self._dlg.get_content_area().pack_start(ctr, True, True, 4)
        bb = Gtk.ButtonBox()
        bb.set_layout(Gtk.ButtonBoxStyle.START)
        bb.show()

        but = Gtk.Button.new_from_icon_name('list-add-symbolic',
                                            Gtk.IconSize.LARGE_TOOLBAR)
        but.set_always_show_image(True)
        but.show()
        but.connect('clicked', self.add_empty)
        bb.pack_start(but, False, False, 0)
        bb.set_child_non_homogeneous(but, True)

        but = Gtk.Button.new_from_icon_name('list-remove-symbolic',
                                            Gtk.IconSize.LARGE_TOOLBAR)
        but.set_always_show_image(True)
        but.show()
        but.connect('clicked', self.del_selected)
        bb.pack_start(but, False, False, 0)
        bb.set_child_non_homogeneous(but, True)

        but = Gtk.Button.new_from_icon_name('pan-up-symbolic',
                                            Gtk.IconSize.LARGE_TOOLBAR)
        but.set_always_show_image(True)
        but.show()
        but.connect('clicked', self.move_up)
        bb.pack_start(but, False, False, 0)
        bb.set_child_non_homogeneous(but, True)

        but = Gtk.Button.new_from_icon_name('pan-down-symbolic',
                                            Gtk.IconSize.LARGE_TOOLBAR)
        but.set_always_show_image(True)
        but.show()
        but.connect('clicked', self.move_down)
        bb.pack_start(but, False, False, 0)
        bb.set_child_non_homogeneous(but, True)

        self._dlg.get_content_area().pack_start(bb, False, False, 4)

    def del_selected(self, button):
        """Delete the selected row."""
        model, i = self._view.get_selection().get_selected()
        if i is not None:
            self._model.remove(i)

    def move_up(self, button):
        """Move selected row up one slot"""
        model, i = self._view.get_selection().get_selected()
        if i is not None:
            j = self._model.iter_previous(i)
            if j is not None:
                self._model.swap(i, j)

    def move_down(self, button):
        """Move selected row down one slot"""
        model, i = self._view.get_selection().get_selected()
        if i is not None:
            j = self._model.iter_next(i)
            if j is not None:
                self._model.swap(i, j)

    def add_empty(self, button):
        """Add an empty row and trigger editing the content"""
        i = self._model.append(('\u2023', ''))
        path = Gtk.TreeModelRow(self._model, i).path
        self._view.set_cursor(path, self._view.get_column(1), True)

    def edit_decision(self, cell, path, new_text, col):
        """Edit column callback."""
        new_text = new_text.strip()
        self._model[path][col] = new_text

    def run(self):
        # for now, ignore dialog return value
        self._dlg.run()
        self._dlg.hide()
        res = [d[1] for d in self._model]
        self._dlg.destroy()
        return res


def decisions_dlg(window=None, decisions=[]):
    """Edit decisions of the commissaires panel and return an updated list"""
    dlg = decisionEditor(window=window, decisions=decisions)
    return dlg.run()
