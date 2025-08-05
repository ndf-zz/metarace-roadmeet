# SPDX-License-Identifier: MIT
"""Relay attached decoder passings, translating channels as required."""

import sys
import logging
import metarace
from time import sleep
from metarace import tod
from metarace import strops
from metarace import jsonconfig
from metarace.telegraph import telegraph
from metarace.decoder.rrs import rrs
from metarace.decoder.rru import rru
from metarace.decoder.thbc import thbc

_log = logging.getLogger('drelay')
_log.setLevel(logging.DEBUG)

# Defaults
_LOGFILE = '.drelay.log'
_TIMERTOPIC = 'timer'
_TIMERQOS = 1
_POLLTIME = 10
_DELAYWARN = 10  # warn if processing delay exceeds this many seconds
_DECODERTYPES = {
    'thbc': 'Chronelec Protime RC/LS',
    'rru': 'RR Active Serial/USB',
    'rrs': 'RR Decoder Active/Passive',
    #'timy': 'Alge Timy Chronometer',
}

_CHANLIST = {
    0: 'C0 [Start]',
    1: 'C1 [Finish]',
    2: 'C2 [PA]',
    3: 'C3 [PB]',
    4: 'C4 [200m]',
    5: 'C5 [100m]',
    6: 'C6 [50m]',
    7: 'C7 [PBQ]',
    8: 'C8 [150m]',
    9: 'C9 [PAQ]',
    -1: 'Ignore'
}

_CONFIG_SCHEMA = {
    'secdec': {
        'prompt': 'Decoder Connection',
        'control': 'section',
    },
    'decodertype': {
        'attr': '_decodertype',
        'control': 'choice',
        'defer': True,
        'prompt': 'Decoder Type:',
        'hint': 'Hardware type of attached decoder',
        'default': None,
        'options': _DECODERTYPES,
    },
    'decoderport': {
        'attr': '_decoderport',
        'defer': True,
        'prompt': 'Decoder Port:',
        'hint': 'Port or IP address of decoder',
        'default': None,
    },
    'deadtime': {
        'attr': '_deadtime',
        'defer': True,
        'prompt': 'Deadtime:',
        'control': 'short',
        'type': 'tod',
        'places': 1,
        'hint': 'Ignore repeated passings for deadtime seconds',
        'default': tod.mktod(2),
    },
    'polltime': {
        'attr': '_polltime',
        'defer': True,
        'prompt': 'Poll Time:',
        'subtext': 'seconds',
        'control': 'short',
        'type': 'int',
        'hint': 'Check decoder status this often',
        'default': _POLLTIME,
    },
    'sectele': {
        'prompt': 'Telegraph',
        'control': 'section',
    },
    'timertopic': {
        'attr': '_timertopic',
        'defer': True,
        'prompt': 'Timer Topic:',
        'hint': 'Telegraph topic to publish passings',
        'default': _TIMERTOPIC,
    },
    'timerqos': {
        'attr': '_timerqos',
        'defer': True,
        'prompt': 'Timer QoS:',
        'control': 'choice',
        'hint': 'Timer message QOS',
        'type': 'int',
        'options': {
            '0': '0 - At most once',
            '1': '1 - At least once',
            '2': '2 - Exactly once'
        },
        'default': _TIMERQOS,
    },
    'secchan': {
        'prompt': 'Channel Re-Mapping',
        'control': 'section',
    },
    'C0': {
        'control': 'choice',
        'prompt': 'C0:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 0 to another channel'
    },
    'C1': {
        'control': 'choice',
        'prompt': 'C1:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 1 to another channel'
    },
    'C2': {
        'control': 'choice',
        'prompt': 'C2:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 2 to another channel'
    },
    'C3': {
        'control': 'choice',
        'prompt': 'C3:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 3 to another channel'
    },
    'C4': {
        'control': 'choice',
        'prompt': 'C4:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 4 to another channel'
    },
    'C5': {
        'control': 'choice',
        'prompt': 'C5:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 5 to another channel'
    },
    'C6': {
        'control': 'choice',
        'prompt': 'C6:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 6 to another channel'
    },
    'C7': {
        'control': 'choice',
        'prompt': 'C7:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 7 to another channel'
    },
    'C8': {
        'control': 'choice',
        'prompt': 'C8:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 8 to another channel'
    },
    'C9': {
        'control': 'choice',
        'prompt': 'C9:',
        'defer': True,
        'options': _CHANLIST,
        'type': 'int',
        'default': None,
        'hint': 'Map timer channel 9 to another channel'
    },
}


class Drelay:

    def __init__(self, dosync=False):
        self._t = telegraph()
        self._d = None
        self._chanmap = {}
        self._recent = {}
        self._decoderport = None
        self._decodertype = None
        self._deadtime = -1
        self._timertopic = _TIMERTOPIC
        self._timerqos = _TIMERQOS
        self._polltime = _POLLTIME
        self._count = 0
        self._delay = 0.0
        self._once = not dosync

    def loadconfig(self):
        cr = jsonconfig.config()
        cr.add_section('drelay', _CONFIG_SCHEMA)
        cr.merge(metarace.sysconf, 'drelay')
        cr.export_section('drelay', self)
        for cid in range(10):
            chan = strops.id2chan(cid)
            mapid = cr.get_value('drelay', chan)
            if mapid is not None:
                self._chanmap[cid] = mapid
                _log.debug('Channel %s mapped to: %d', chan, mapid)
        if self._decodertype == 'thbc':
            self._d = thbc()
        elif self._decodertype == 'rru':
            self._d = rru()
        elif self._decodertype == 'rrs':
            self._d = rrs()
        else:
            raise RuntimeError('Invalid decoder type %r' % (self._decodertype))
        if self._polltime < 1:
            self._polltime = 1

    def start(self):
        self._t.start()
        self._d.start()
        self._d.setcb(self.passing)
        _log.info('Polling decoder status @ %d s', self._polltime)

    def passing(self, event):
        cid = strops.chan2id(event.chan)
        if cid in self._chanmap:
            cid = self._chanmap[cid]
        if cid >= 0:  # ignore invalid channel ID: -1
            since = self._deadtime + tod.ONE
            if event.refid in self._recent:
                since = event - self._recent[event.refid]
            self._recent[event.refid] = event
            if since > self._deadtime:
                # check for loss of sync
                pt = tod.now()
                delay = abs(pt.timeval - event.timeval)
                self._delay = max(self._delay, delay)
                if delay > _DELAYWARN:
                    _log.warning('Possible sync loss, delay=%0.1f', delay)
                self._count += 1

                event.chan = strops.id2chan(cid)
                source = ''
                if event.source:
                    source = event.source
                b = (event.timeval * 0).as_tuple()
                places = min(-(b.exponent), 5)
                timestr = event.isostr(places)
                msg = ';'.join(
                    (event.index, source, event.chan, event.refid, timestr))
                self._t.publish(topic=self._timertopic,
                                message=msg,
                                qos=self._timerqos)
                _log.info('%s', msg)
            else:
                _log.debug('Ignored passing during deadtme')
        else:
            _log.debug('Ignored passing')

    def poll(self):
        if not self._d.connected():
            self._d.setport(self._decoderport)
            if not self._once:
                sleep(0.1)
                _log.info('Requesting new session')
                self._d.clear()
        else:
            if not self._once:
                self._once = True
            self._d.status()
            refs = len(self._recent)
            _log.info('%d passing%s, %d refid%s, max delay ~%0.1fs',
                      self._count, strops.plural(self._count), refs,
                      strops.plural(refs), self._delay)

        sleep(self._polltime)


def main():
    # attach log handlers to the root logger
    fh = logging.Formatter(metarace.LOGFORMAT)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)  # show info+ on console
    ch.setFormatter(fh)
    lh = logging.FileHandler(_LOGFILE)
    lh.setLevel(logging.DEBUG)  # include debug+ in logfile
    lh.setFormatter(fh)
    rl = logging.getLogger()
    rl.addHandler(ch)
    rl.addHandler(lh)

    # check command line
    dosync = False
    if len(sys.argv) > 1:
        if sys.argv[1] == '-sync':
            dosync = True
        else:
            print('Usage: drelay [-sync]')
            sys.exit(-1)

    # init library and app
    metarace.init()
    app = Drelay(dosync)
    app.loadconfig()
    app.start()

    # check connection periodically
    while True:
        app.poll()


if __name__ == '__main__':
    sys.exit(main())
