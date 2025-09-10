""" Attempt at driver for Elecraft K- and KX-Series HF radios,
    pretending the Elecraft is a clone-mode radio rather than live.
    sync_in and sync_out access all memories via serial port, making it
    slowish, but it doesn't write to the radio for every change of a line
    in the CHIRP GUI as a live-radio wants to do.
    Adapted from template.py
    Known failings (July 2025):
    - Tones do not display correctly
    - Offsets do not display correctly
    - Has been tested only with KX3. Since programmer's manuals are
      the same, this all may also work for K3, KX2 and maybe K4,
      so I've foolishly included their definitions too.
    - Has not yet been tested to write to radio. The driver keeps tabs of
      radio memories that have changed, so it should only write changed
      memories back to the radio.
    - Doesn't handle transverters, which would extend frequency ranges
    - Doesn't handle band-specific or radio-specific settings
"""
# Copyright 2024, 2025 Declan Rieb <wd5eqy@arrl.net>
# Copyright 2012 Dan Smith <dsmith@danplanet.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
# import pdb              # Debug requiremnt
# from watchpoints import watch   # Debug requirement
import string
import time
import threading
import serial
from chirp import chirp_common
from chirp import bitwise
from chirp import directory
from chirp import errors
from chirp import memmap
from chirp.settings import RadioSetting, RadioSettingGroup, \
                    RadioSettingValueList, RadioSettingValueString, \
                    RadioSettingValueBoolean

LOG = logging.getLogger(__name__)

# Here is where we define the memory map for the radio. Since
# frequency (in Hertz) and an eight-character alpha tag
# As a choice, the radio memories are kept as binary copies of the
#    text commands that are read, after stripping the two-character
#    command and the end-of-line terminator.
# To write, simply calculate the checksum, convert to hex text
#    and add the missing opcode and end-of-line..
# Most fields correspond with descriptions in "k3mem" documentation,
#    <https://github.com/ik5pvx/k3mem> due to IK5PVX, Pierfrancesco Caci
#    et alii in 2011.
MEM_FORMAT = """
struct {
    u32 word;
    } lomem[64];
struct {            // Must be at 0x100
    u8  vfoa[5];
    u8  vfob[5];
    u8  state0;
    u8  state1;
    u8  state2;
    u8  state3;
    u8  state4;
    i8  number;
    }   bandstate[25];

#seekto 0x02A2;
struct {
    u8  state[10];
    }   xvrtrstate[9];

#seekto 0x0C00;
struct {
    u8  freq[5];        // This really is vfoa, but CHIRP needs a freq
    u8  vfob[5];
    u8  modeb:4,
        modea:4;
    u8  digimode:4,     // Submode if either mode is DATA
        b75:1,          // 0-> 45/31 Baud, 1-> 75 Baud
        lsb:1,          // lsb, normally lower sideband
        unk:1,
        digit:1;        // supposedly always one for DATA modes
    u8  ant:1,          // 0-> Antenna 1, 1-> Antenna 2
        rev:1,          // Reverse mode (CW-R, DATA-R, AM-S)
        nb:1,           // noise blanker?
        unk0:1,
        pre:1,          // Preamplifier?
        unk01:2,
        att:1;          // Attenuator
    u8  rxant:1,        // Receive antenna
        flag3:6,
        split:1;        // Split mode (TX A, RX B)
    u8  flag4;
    u8  unk1:3,
        xv:1,           // Xverter in use
        band:4;         // 0-0xA (normal bands) 0-8 (XV1-XV9)
    u8  subtone;
    u8  offset;         // 00-0xFA, 20kHz/step
    u8  unk3:5,         // Repeater flags
        tone:1,         // Tone off/on
        minus:1,        // 0 -> shift+  1 -> shift-
        duplex:1;       // simplex/duplex
    u32 flags;
    u8  unknown[9];
    u8  label[5];       // Displayed with memory... NOT ASCII
    char  comment[24];
    u8  unk4;
    u8  unk5;
    u8  unk6;
    } radiomem[200];
"""

# All Elecraft commands driver uses:
READ_CMD = "ER"     # MC on K4
WRIT_CMD = "EW"     # MC on K4
IDEN_CMD = "ID"
XTRA_CMD = "OM"
AUTO_CMD = "AI"

MINMEM = 0x0000
MAXMEM = 0x4140
MEMLEN = 0x40
MEM_START = 0x0C00
MEM_LEN = 0X40

# Identifiers for the different bands used in Quick Memories
BNAME = ['160', '80', '60', '40', '30', '20', '17', '15', '12', '10', '6',
         'Rs1', 'Rs2', 'Rs3', 'Rs4', 'Rs5',
         'XV1', 'XV2', 'XV3', 'XV4', 'XV5', 'XV6', 'XV7', 'XV8', 'XV9'
         ]
# Identifiers for the four Quick Memories in each band
QTYPE = [' M1', ' M2', ' M3', ' M4']
SPECIALS = [BNAME[_i] + QTYPE[_j]
            for _i in range(0, 25) for _j in range(0, 4)]

# in Elecraft, the many modes correspond with (at least!) five different
#   settings, so a dict is used to do the translations
# dict of Modes and corresponding settings [mode, submode, lsb, rev, flag4]
ALLMODESd = {
             'AM':          [4, 0, 0, 0, 0],
             'AM-S/USB':    [4, 0, 0, 0, 0x0a],
             'AM-S/LSB':    [4, 0, 0, 0, 0x08],
             'CW':          [0, 0, 0, 0, 0],
             'CW-Rev':      [0, 0, 0, 1, 0],
             'DATA A':      [3, 0, 1, 0, 0],
             'DATA A/Rev':  [3, 0, 0, 0, 0],
             'FM':          [5, 0, 0, 0, 0],
             'LSB':         [1, 0, 0, 0, 0],
             'USB':         [2, 0, 0, 0, 0],
             'AFSK-A':      [3, 2, 1, 0, 0],
             'FSK-D':       [3, 4, 1, 0, 0],
             'PSK-D':       [3, 6, 1, 0, 0],
             'DATA-A/Rev':  [3, 0, 0, 0, 0],
             'AFSK-A/Rev':  [3, 2, 0, 0, 0],
             'FSK-D/Rev':   [3, 4, 0, 0, 0],
             'PSK-D/Rev':   [3, 6, 0, 0, 0],
            }
ALLMODES = list(ALLMODESd.keys())   # Just the mode names, for CHIRP
LAM = len(ALLMODES)
Elecraft_TONES = [0.0] + list(chirp_common.TONES) + [1750.0]
LET = len(Elecraft_TONES)

# Elecraft radios (<K4?) don't use ASCII
VALID_CHARS = ' ' + string.ascii_uppercase + string.digits + '*+/@_'

# make byte-translation tables between 'ascii' and KX-characters
A2KX = bytes.maketrans(VALID_CHARS.encode('cp1252').ljust(256, b'.'),
                       bytes(range(256)))
KX2A = bytes.maketrans(bytes(range(256)),
                       VALID_CHARS.encode('cp1252').ljust(256, b'.'))

RADIO_IDS = {       # ID always returns 017, except for K4
                    # periods mean "i don't care"
    "ID017": "Elecraft",
    "ID017X": "K3",     # OM returns "..........--"
    "ID0171": "KX2",    # OM returns "..........01"
    "ID0172": "KX3",    # OM returns "..........02"
    "ID0174": "K4"      # OM returns "........4---"
}

NMEMS = 100   # Number of Regular MEMorieS
# NMEMS = 4   # Number of Regular MEMorieS
# NSPLS = 4   # Number of SPeciaLS
NSPLS = 100   # Number of SPeciaLS

OSTEP = 20000       # 20kHz per offset step in radio
LOFFSET = 0xFA      # Maximum value of offset field in radio; 5MHz

LOCK = threading.Lock()
COMMAND_RESP_BUFSIZE = 200
LAST_BAUD = 38400
LAST_DELIMITER = (";", "")
FF5 = [0xff, 0xff, 0xff, 0xff, 0xff]

# The Elecraft radios use ";"
# as a CAT command message delimiter, and all others use "\n".


def _command(port: serial.serialposix.Serial, cmd: str, *args) -> str:
    """ _command sends the "cmd" with "args" over serial port "ser"
        AND THEN reads and returns the response from same port. """
    # Send @cmd to radio via @ser
    # DAR    global LOCK, LAST_DELIMITER, COMMAND_RESP_BUFSIZE

    start = time.time()

    # yet to do: This global use of LAST_DELIMITER breaks reentrancy
    # and needs to be fixed.
    if args:
        cmd += LAST_DELIMITER[1] + LAST_DELIMITER[1].join(args)
    cmd += LAST_DELIMITER[0]

    LOG.debug("PC->RADIO: %s" % cmd.strip())
    port.write(cmd.encode('cp1252'))

    result = ""
    while not result.endswith(LAST_DELIMITER[0]):
        result += port.read(COMMAND_RESP_BUFSIZE).decode('cp1252')
        if (time.time() - start) > 1:
            LOG.debug("Timeout waiting for data")
            break

    if result.endswith(LAST_DELIMITER[0]):
        LOG.debug("RADIO->PC: %s" % result.strip())
        result = result[:-1]        # remove delimiter
    else:
        LOG.debug("Giving up")

    return result.strip()


def command(port: serial.serialposix.Serial, cmd: str, *args) -> str:
    """ send serial command inside a LOCK-protected spot """
    with LOCK:
        return _command(port, cmd, *args)


def get_id(port: serial.serialposix.Serial) -> str:
    """ Get the ID and type of the radio attached to @ser
        port    is the serial port to use
        returns the model string of the radio
    """
    global LAST_BAUD
    bauds = [4800, 9600, 19200, 38400, 57600, 115200]
    bauds.remove(LAST_BAUD)
    # Make sure LAST_BAUD is last so that it is tried first below
    bauds.append(LAST_BAUD)

    global LAST_DELIMITER
    command_delimiters = [(";", "")]

    for delimiter in command_delimiters:
        # Process the baud options in reverse order so that we try the
        # last one first, and then start with the high-speed ones next
        for i in reversed(bauds):
            LAST_DELIMITER = delimiter
            LOG.info(
                "Trying ID at baud %d with delimiter '%s'", i, delimiter)
            port.baudrate = i
            port.write(LAST_DELIMITER[0].encode())
            port.read(25)
            try:
                resp = command(port, IDEN_CMD)
            except UnicodeDecodeError:
                # If we got binary here, we are using the wrong rate
                # or not talking to a elecraft live radio.
                continue

            # most elecraft radios
            if " " in resp:
                LAST_BAUD = i
                return resp.split(" ")[1]

            # Radio responded in the right baud rate,
            # but threw an error because of all the crap
            # we have been hurling at it. Retry the ID at this
            # baud rate, which will almost definitely work.
            if '?' in resp:
                resp = command(port, IDEN_CMD)
                LAST_BAUD = i
                if ' ' in resp:
                    return resp.split(' ')[1]

            # elecraft radios that return ID numbers, ask for more info
            if resp in RADIO_IDS:
                xt = command(port, XTRA_CMD)
                if '4---' == xt[-4:]:
                    resp = 'ID0174'
                elif '--' == xt[-2:]:
                    resp = 'ID017X'
                elif '01' == xt[-2:]:
                    resp = 'ID0171'
                elif '02' == xt[-2:]:
                    resp = 'ID0172'
                return RADIO_IDS[resp]

    raise errors.RadioError('No response from radio')


def _cmd_get_memory(number: int | str) -> str:
    ''' returns a string with radio command to read a memory from radio.
        The return includes a one-byte checksum
        '''
    address = MEM_START + number * MEM_LEN
    message = f'{address:04x}{MEM_LEN:02x}'
    checksum = ((sum(bytearray.fromhex(message)) - 1) & 0xFF) ^ 0xFF
    message = f'{READ_CMD}{message}{checksum:2x}'
    return message


def _cmd_set_memory(number: int, mem: bytes = None) -> str:
    """ returns string with the appropriate write command
        number  is the CHIRP memory number
        mem     data bytes to include in the command
        """
    _b = bytearray(mem)
    address = MEM_START + number * MEM_LEN
    # DAR length = MEM_LEN
    print(f"_cmd_set_memory: {number} {address:04x} \n{mem}")
    _c1 = -sum(mem[:-1]) % 0x100
    if (-_c1 + mem[-1]) & 0xFF != 0:
        print('Checksum Error!')
    _b[-1] = _c1.to_bytes(4, signed=True)[-1]
    message = bytes(_b).hex()
    print(f'_cmd_set_memory: {_c1:x}, {(_c1 + mem[-1]):x}')
    return f"{WRIT_CMD}{message}"


def radio_to_freq(_b: list) -> int:
    """ Turns the five hex bytes into integer frequency """
    freq = int(_b[0]) * 1000000 + int(_b[1]) * 10000 + int(_b[2]) * 100 +\
        int(_b[3]) * 10 + int(_b[4])
    return freq if list(_b) != FF5 else 0


def freq_to_radio(f: int) -> list:
    """ Turn an integer frequency into an Elecraft 5-byte string """
    _f = f
    _bm = _f // 1000000
    _f -= _bm * 1000000
    _bT = _f // 10000
    _f -= _bT * 10000
    _bt = _f // 100
    _f -= _bt * 100
    _bh = _f // 10
    _f -= _bh * 10
    _bo = _f
    byt = [_bm, _bT, _bt, _bh, _bo]
    return byt if f != 0 else FF5


class ECMemory(chirp_common.Memory):
    """
    This is a clone of CHIRP's Memory, but allows the driver to redfine
    some critical items, such as modes, tones and default parameters
    Name is short for "Elecraft version of Chirp Memory"
    """

    def __init__(self, number=0, empty=False, name=""):
        super().__init(self, number, empty, name)
        self.offset = 0
        self.rtone = 0.0

    _valid_map = {
        '''
        CHIRP enforces that all rf.valid... values must be subsets of the
        following. This clone of the data allows the driver to use more
        lax restraints.
        '''
        "rtone":          Elecraft_TONES,               # Elecraft mod,
        "ctone":          chirp_common.VALIDTONE,
        "dtcs":           chirp_common.ALL_DTCS_CODES,
        "rx_dtcs":        chirp_common.ALL_DTCS_CODES,
        "tmode":          chirp_common.TONE_MODES,
        "dtcs_polarity":  ["NN", "NR", "RN", "RR"],
        "cross_mode":     chirp_common.CROSS_MODES,
        "mode":           ALLMODES,                     # Elecraft mod
        "duplex":         ["", "+", "-", "split", "off"],
        "skip":           chirp_common.SKIP_VALUES,
        "empty":          [True, False],
        "dv_code":        [x for x in range(0, 100)],
    }


@directory.register
class ElecraftRadio(chirp_common.CloneModeRadio):
    """ Base class for all K- and KX-series Elecraft Radios """

    VENDOR = "Elecraft"
    MODEL = "K[X]-series"
    BAUD_RATE = 38400
    NEEDS_COMPAT_SERIAL = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(self, *args, **kwargs)
        if self.pipe:
            self.pipe.timeout = 0.1
            # Turn off Auto-info Mode, so no radio interruptions
            command(self.pipe, AUTO_CMD,  "0")
            # Ask the radio to identify itself
            radio_id = get_id(self.pipe)
            if radio_id != self.MODEL.split(" ", 1)[0]:
                raise errors.RadioError(
                    f"Radio reports {radio_id} not {self.MODEL}")
        self.Changed = [False for _ in range(200)]
        self.get_features()

    def get_features(self) -> chirp_common.RadioFeatures:
        '''
        Return information about this radio's features, including
        how many memories it has, what bands it supports, etc
        CHIRP calls this many times during execution, so it must be lightweight
        '''
        rf = super().RadioFeatures()
        rf.can_odd_split = True
        rf.has_bank = False
        rf.has_bank_index = False
        rf.has_bank_names = False
        rf.has_comment = True
        rf.has_ctone = False
        rf.has_dtcs = False
        rf.has_dtcs_polarity = False
        rf.has_mode = False
        rf.has_name = True
        rf.has_nostep_tuning = True
        rf.has_offset = True
        rf.has_settings = False
        rf.has_tuning_step = False
        rf.memory_bounds = (0, NMEMS - 1)  # This radio supports memories 0-99
        rf.valid_bands = [
                         (310000, 32000000),   # True for KX3 w/o xvrtr
                         (44000000, 54000000),
                        ]
        rf.valid_characters = VALID_CHARS
        rf.valid_duplexes = []
        rf.valid_modes = ALLMODES
        rf.valid_name_length = 5
        rf.valid_special_chans = SPECIALS[:NSPLS]   # limit length for debug
        return rf

    def sync_checksum(self, hex: str) -> int:
        """ build Elecraft memory-request checksum from hex, returned as int
        """
        b = bytes.fromhex(hex)[:-1]
        return (((sum(b) - 1) & 0xff) ^ 0xff)

    def process_mmap(self) -> None:
        """ does the actual parsing of _mmap into _memobj.
            This is needed because chirp_common calls it to handle a read _mmap
            and the chirp_common version does nothing.
            Chaged[] is also preset to False entries.
        """
        self._memobj = bitwise.parse(MEM_FORMAT, self._mmap)
        self.Changed = [False for _ in range(200)]

    def sync_in(self) -> None:
        """ This queries radio port for all regular and special memories
            then converts ASCII to Elecraft internal format
            Side effects are:
            The _mmap is the catenation of all memory binary data
            processmmap() parses the _mmap into _memobj structure
                and defines Changed[] for all memories
        """
        # Get the serial port connectiona
        port = self.pipe
        port.timeout = 0.1
        status = chirp_common.Status()
        status.max = MAXMEM - MINMEM + 1
        status.msg = "Reading from Radio"

        self._mmap = bytearray()
        _data = b''
        # For each regular memory, read radio and convert to memory image
        for _i in range(MINMEM, MAXMEM, MEMLEN):
            _idx = (_i - MINMEM) // MEMLEN
            # Don't read all memories if NMEMS or NSPLS are not 100
            if not (_idx < NMEMS or 99 < _idx <= 99 + NSPLS):
                continue
            _cmd = f'{_i:04x}{0x40:02x}'
            _csum = self.sync_checksum((_cmd + '00'))
            _line = command(port, 'ER' + _cmd + f'{_csum:2x}')
            # get all data; not command, addres, length and terminating ';'
            _data += bytes.fromhex(_line[8:-2])
            # Update thermometer bar on GUI
            status.cur = _i
            self.status_fn(status)
        self._mmap = memmap.MemoryMapBytes(_data)
        self.process_mmap()
        status.cur += 1
        self.status_fn(status)

    def sync_out(self) -> None:
        """
        Upload only changed memory lines to radio via serial port
        """
        port = self.pipe
        port.timeout = 0.1
        status = chirp_common.Status()
        status.max = MAXMEM - MINMEM + 1
        status.msg = "writing to Radio"

        _data = b''
        # For each regular memory, read radio and convert to memory image
        for _addr in range(MEM_START, MAXMEM, MEMLEN):
            _i = (_addr - MEM_START) // MEMLEN
            # Ignore unchanged data
            if not self.Changed[_i]:
                continue
            _data = self._mmap[_addr: _addr + 0x40]
            _cmd = f'{_addr:04x}{0x40:02x}' + _data.hex()
            _csum = self.sync_checksum((_cmd + '00'))
            # Uncomment following for read write to USB port
            # _line = command(port, 'ER' + _cmd + f'{_csum:2x}')
            print(f'sync_out[{_i}=ER{_cmd}{_csum}')
            self.Changed[_i] = False
            # Update thermometer bar on GUI
            status.cur = _i
            self.status_fn(status)

    def get_raw_memory(self, number: int) -> str:
        """
        Return a raw representation of the memory object, which
            is very helpful for development.
        """
        return repr(self._memobj.radiomem[number])

    def get_memory(self, number: int | str) -> ECMemory:
        """ Extract a CHIRP-level memory object from the radio-level map
            This is called to populate a memory in the UI

            Probably should NOT be changing radio memory (_mem) here
        """
        # Create a CHIRP-level memory object to return to the UI
        mem = self.ECMemory()
        mem.extra = RadioSettingGroup('extra', 'Extra')
        LOG.debug(f'get_memory entry, number={number}')
        if isinstance(number, int):
            mem.number = number                 # Set CHIRP memory number
        elif isinstance(number, str | bytes):
            mem.extd_number = number
            mem.number = NMEMS + SPECIALS.index(number)

        # Radio memory not defined? why? how can this happen?
        if not self._memobj:
            mem.empty = True
            LOG.warning('get_memory: no _memobj')
            return mem
        if not self._memobj.radiomem:
            mem.empty = True
            LOG.warning('get_memory: no _memobj.radiomem')
            return mem
        # Get radio-level memory element corresponding to mem.number
        _mem = self._memobj.radiomem[mem.number]
        LOG.debug(f'get_memory entry, Radio _mem={_mem}')

        # whole radio memory is blank, so few changes but empty.
        # this looks at the underlying radio bytes, not the data structure
        _radd = mem.number * 0x40 + 0xC00
        _memFrom_mmap = self._mmap[_radd: _radd + 0x40]
        # Some radio memory setters have a set of zeroes, but mostly FF
        _nulr = True if len(_memFrom_mmap.rstrip(b'\xFF\x00')) <= 4 else False

        # Required to get CHIRP to show tones. Settable for each memory.
        mem.tmode = 'Tone'

        # Radio has null memory. preset unchecked-for items:
        svfoa = chirp_common.format_freq(radio_to_freq(_mem.freq))
        _rs = RadioSetting('vfoa', 'VFO A (MHz)',
                           RadioSettingValueString(0, 10, svfoa))
        mem.extra.append(_rs)

        # This complex list includes all the things that define a
        # particular, labelled mode in KX3, and is especially important
        # to distinguish between the various data modes
        # N.b: using _nulr so I don't check for 1, 3, 7 or ff on every item.
        if _mem.modea == 3:     # data mode
            _a = [3 if _mem.modea == 0x0f else int(_mem.modea),
                  0 if _mem.digimode == 0x0f else int(_mem.digimode),
                  0 if _nulr else int(_mem.lsb),
                  0 if _nulr else int(_mem.rev),
                  0 if _mem.flag4 == 0xff else int(_mem.flag4)]
        else:                   # not data modes; default to AM
            _a = [4 if _mem.modea == 0x0f else int(_mem.modea),
                  0,
                  0,
                  0 if _nulr else int(_mem.rev),
                  0 if _mem.flag4 == 0xff else int(_mem.flag4)]

        _ai = list(ALLMODESd.values()).index(_a)
        _rs = RadioSetting('modea', 'Mode A',
                           RadioSettingValueList(ALLMODES, None, _ai))
        mem.extra.append(_rs)

        svfob = chirp_common.format_freq(radio_to_freq(_mem.vfob))
        if _mem.modeb == 3:     # Data mode
            _b = [3 if _mem.modeb == 0x0f else int(_mem.modea),
                  0 if _mem.digimode == 0x0f else int(_mem.digimode),
                  0 if _nulr else int(_mem.lsb),
                  0 if _nulr else int(_mem.rev),
                  0 if _mem.flag4 == 0xff else int(_mem.flag4)]
        else:                   # NOT data mode; default to AM
            _b = [4 if _mem.modeb == 0x0f else int(_mem.modeb),
                  0,
                  0,
                  0 if _nulr else int(_mem.rev),
                  0 if _mem.flag4 == 0xff else int(_mem.flag4)]

        _rs = RadioSetting('vfob', 'VFO B (MHz)',
                           RadioSettingValueString(0, 10, svfob))
        mem.extra.append(_rs)

        _bi = list(ALLMODESd.values()).index(_b)
        _rs = RadioSetting('modeb', 'Mode B',
                           RadioSettingValueList(ALLMODES, None, _bi))
        mem.extra.append(_rs)

        _t1750 = True if not _nulr and _mem.subtone > LET else False
        _rs = RadioSetting('burst', '1750Hz Tone Burst',
                           RadioSettingValueBoolean(_t1750))
        mem.extra.append(_rs)

        _rs = RadioSetting('change', 'Write',
                           RadioSettingValueBoolean(self.Changed[mem.number]))
        mem.extra.append(_rs)

        # freq in CHIRP is VFO A in radio
        if list(_mem.freq) == FF5 and mem.freq != 0:
            _mem.freq = chirp_common.format_freq(mem.freq)

        mem.empty = False
        # ensure freq in CHIRP is same as VFO A in radio
        mem.freq = radio_to_freq(list(_mem.freq))
        mem.mode = ALLMODES[_mem.modea]
        mem.name = '' if list(_mem.label) == FF5 else\
            bytes(_mem.label).translate(KX2A).decode('cp1252')[:5].rstrip()
        mem.comment = str(_mem.comment).strip('\xFF').rstrip()
        mem.offset = 0 if _mem.offset == b'\xff' else\
            int(_mem.offset) * OSTEP
        mem.duplex = 'off' if mem.offset == 0 else\
                     '-' if int(_mem.minus) == 1 else '+'
        # 1750 Hz burst is handled separately as an extra
        mem.rtone = Elecraft_TONES[0] if int(_mem.subtone) > LET else\
            Elecraft_TONES[max(0, _mem.subtone)]

        # The radio was [almost] all 0xFF, so mark it empty
        if _nulr:
            mem.empty = True
        mem.immutable = ['skip', 'mode']
        LOG.debug(f'get_memory exit: CHIRP mem="{mem}", {mem.extra}')
        return mem

    def blank_mem(self, _mem) -> None:
        ''' If CHIRP says memory is empty, enter 0xFF for every byte in radio
            _mem is a CHIRP radiomem structure
        '''
        _mem.freq = FF5
        _mem.vfob = FF5
        _mem.modeb = 0x0f
        _mem.modea = 0x0f
        _mem.digimode = 0x0f
        _mem.b75 = 1
        _mem.lsb = 1
        _mem.unk = 1
        _mem.digit = 1
        _mem.ant = 1
        _mem.rev = 1
        _mem.nb = 1
        _mem.unk0 = 1
        _mem.pre = 1
        _mem.unk01 = 3
        _mem.att = 1
        _mem.rxant = 1
        _mem.flag3 = 0x3f
        _mem.split = 1
        _mem.flag4 = 0xff
        _mem.unk1 = 7
        _mem.xv = 1
        _mem.band = 0x0f
        _mem.subtone = 0xff
        _mem.offset = 0xff
        _mem.unk3 = 0x1f
        _mem.tone = 1
        _mem.minus = 1
        _mem.duplex = 1
        _mem.flags = 0xffffffff
        _mem.unknown = FF5 + FF5[:-1]
        _mem.label = b'\xff' * 5
        _mem.comment = b'\xff' * 24
        _mem.unk4 = 0xff
        _mem.unk5 = 0xff
        _mem.unk6 = 0xff

    def set_memory(self, memory: ECMemory) -> None:
        ''' Store details about a CHIRP-level memory to the radio's map
            This is called when a user edits a memory in the UI

            MUST not change CHIRP memory here.
        '''
        # Radio memory not defined? why? how can this happen?
        # It happens in CHIRP tests, so checks occur:
        if not self._memobj:
            raise errors.RadioError(f'No Radio _memobj instance "{memory}"')
        if not self._memobj.radiomem:
            raise errors.RadioError(f'No Radio radiomem instance "{memory}"')
        if not memory.extra:
            raise errors.RadioError(
                f'Invalid CHIRP memory: no extra in "{memory}"')

        # Get radio-level memory element corresponding to mem.number
        _mem = self._memobj.radiomem[memory.number]
        if memory.empty:
            self.blank_mem(_mem)
            return
        # if CHIRP freq is nonzero and radio VFO A null, use freq
        LOG.debug(f'_mem.freq={type(_mem.freq)}')
        # set radio memories' otherwise-unused fields to pass tests
        if memory.freq != 0 and list(_mem.freq) == FF5:
            _mem.freq = freq_to_radio(memory.freq)
        else:
            _mv = chirp_common.parse_freq(str(memory.extra['vfoa'].value))
            _mem.freq = freq_to_radio(_mv)
        _modea = str(memory.extra['modea'].value)
        [_mem.modea, _d, _l, _r, _f] = ALLMODESd[_modea]
        # Mode A takes precedence for digital modes
        _mem.digimode = _d
        _mem.lsb = _l
        _mem.rev = _r
        _mem.flag4 = _f
        _mem.vfob = freq_to_radio(
                    chirp_common.parse_freq(str(memory.extra['vfob'].value)))
        _modeb = str(memory.extra['modeb'].value)
        [_mem.modeb, _d, _l, _r, _f] = ALLMODESd[_modeb]
        # Mode A takes precedence for digital modes, but B can be used
        if _mem.modeb == 3 and _mem.modea != 3:
            _mem.digimode = _d
            _mem.lsb = _l
            _mem.rev = _r
            _mem.flag4 = _f

        _mem.label = bytes(memory.name.ljust(5), 'cp1252')[:5].translate(A2KX)
        _mem.subtone = LET + 1 if memory.extra['burst'].value else \
            Elecraft_TONES.index(memory.rtone)
        _mo = memory.offset // OSTEP
        _mem.offset = 0 if _mo > LOFFSET else _mo
        _mem.minus = 1 if memory.duplex == '-' else 0
        _mem.comment = memory.comment.ljust(24)[:24]
        self.Changed[memory.number] = True

    @classmethod
    def get_prompts(cls) -> chirp_common.RadioPrompts:
        _rp = chirp_common.RadioPrompts()
        _rp.experimental = _(
            "This Elecraft driver is experimental and may be unstable.\n"
            "It's been tested with Elecraft KX3, but K3, and KX2 share APIs.\n"
            "K4 is similar enough that only a few changes may be needed.\n"
            "Writing to KX3 has not yet been fully validated. 2025-08"
            )
        return _rp

    @classmethod
    def match_model(cls, filedata, filename):
        print(f'match_model {filedata[:5].hex()} {cls._model.hex()}'
              f'{len(filedata)} {cls._memsize}')
        return filedata[:5] == cls._model and len(filedata) == cls._memsize


@directory.register
class KX3Radio(ElecraftRadio,
               chirp_common.ExperimentalRadio):
    """ Class for Elecraft KX3 radio. DAR"""
    MODEL = "KX3"
    _model = b'\x17FB\x00\x8a'
    _memsize = 0x4040


@directory.register
class KX2Radio(ElecraftRadio):
    """ Class for Elecraft KX2 radio.  DAR"""
    MODEL = "KX2"

    def get_features(self):
        super().get_features()
        rf = chirp_common.RadioFeatures()
        rf.valid_modes = ["AM", "USB", "LSB", "CW", "FM", "FSK", "RTTY"]
        rf.valid_bands = [(500000, 32000000),   # Valid for KX2 w/o xvrtr
                          ]
        return rf


@directory.register
class K3Radio(ElecraftRadio):
    """ Class for Elecraft KX radio. DAR"""
    MODEL = "K3"


@directory.register
class K4Radio(ElecraftRadio, chirp_common.ExperimentalRadio):
    """ Class for Elecraft K4 radio. DAR"""
    MODEL = "K4"
    READ_CMD = 'MC'
    WRIT_CMD = 'MC'
