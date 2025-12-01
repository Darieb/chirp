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
''' Template.py modified to allow playing with settings.
    DAR 2025
    '''

from chirp import chirp_common, memmap
from chirp import bitwise
from chirp import directory
from chirp.settings import RadioSetting, RadioSettings, \
    RadioSettingGroup, RadioSettingSubGroup, \
    RadioSettingValueBoolean

# Here is where we define the memory map for the radio. Since
# We often just know small bits of it, we can use #seekto to skip
# around as needed.
#
# Our fake radio includes just a single array of ten memory objects,
# With some very basic settings, a 32-bit unsigned integer for the
# frequency (in Hertz) and an eight-character alpha tag
#
MEM_FORMAT = """
#seekto 0x0000;
struct {
  u32 freq;
  char name[8];
} memory[10];
"""


def do_download(radio):
    """This is your download function"""
    # NOTE: Remove this in your real implementation!
    return memmap.MemoryMapBytes(b"\x00" * 1000)

    # Get the serial port connection
    serial = radio.pipe

    # Our fake radio is just a simple download of 1000 bytes
    # from the serial port. Do that one byte at a time and
    # store them in the memory map
    data = b""
    for _i in range(0, 1000):
        data += serial.read(1)

    return memmap.MemoryMapBytes(data)


def do_upload(radio):
    """This is your upload function"""
    # NOTE: Remove this in your real implementation!
    raise Exception("This template driver does not really work!")

    # Get the serial port connection
    serial = radio.pipe

    # Our fake radio is just a simple upload of 1000 bytes
    # to the serial port. Do that one byte at a time, reading
    # from our memory map
    for i in range(0, 1000):
        serial.write(radio.get_mmap()[i])


# Uncomment this to actually register this radio in CHIRP
@directory.register
class TemplateRadio(chirp_common.CloneModeRadio):
    """Acme Template"""
    VENDOR = "Acme"     # Replace this with your vendor
    MODEL = "Template Settings"  # Replace this with your model
    BAUD_RATE = 9600    # Replace this with your baud rate

    def __init__(self, port):
        super().__init__(port)
        self.foo = []
        self.oof = []

    # All new drivers should be "Byte Clean" so leave this in place.
    # Return information about this radio's features, including
    # how many memories it has, what bands it supports, etc
    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_bank = False
        rf.has_settings = True
        rf.memory_bounds = (0, 9)  # This radio supports memories 0-9
        rf.valid_bands = [(144000000, 148000000),  # Supports 2-meters
                          (440000000, 450000000),  # Supports 70-centimeters
                          ]
        return rf

    # Do a download of the radio from the serial port
    def sync_in(self):
        self._mmap = do_download(self)
        self.process_mmap()

    # Do an upload of the radio to the serial port
    def sync_out(self):
        do_upload(self)

    # Convert the raw byte array into a memory object structure
    def process_mmap(self):
        self._memobj = bitwise.parse(MEM_FORMAT, self._mmap)

    # Return a raw representation of the memory object, which
    # is very helpful for development
    def get_raw_memory(self, number: int) -> str:
        return repr(self._memobj.memory[number])

    # Extract a high-level memory object from the low-level memory map
    # This is called to populate a memory in the UI
    def get_memory(self, number: int) -> chirp_common.Memory:
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[number]

        # Create a high-level memory object to return to the UI
        mem = chirp_common.Memory()

        mem.number = number                 # Set the memory number
        # Convert your low-level frequency to Hertz
        mem.freq = int(_mem.freq)
        mem.name = str(_mem.name).rstrip()  # Set the alpha tag

        # We'll consider any blank (i.e. 0 MHz frequency) to be empty
        if mem.freq == 0:
            mem.empty = True

        return mem

    # Store details about a high-level memory to the memory map
    # This is called when a user edits a memory in the UI
    def set_memory(self, memory: chirp_common.Memory):
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[memory.number]
        _mem.freq = memory.freq
        _mem.name = memory.name.ljust(8)[:8]  # Store the alpha tag

    def gs1(self) -> RadioSettingGroup:
        """ returns RadioSettingGroup for first batch.
            This should have tooltips """
        menu = RadioSettingGroup('gs1', 'Group 1')
        rs = RadioSettingSubGroup('first part', 'First Part')
        menu.append(rs)
        val = RadioSettingValueBoolean(self.bar[0])
        rs = RadioSetting('bar[0]', 'bar value 0', val)
        rs.set_doc('This is text for bar[0]')
        menu.append(rs)
        val = RadioSettingValueBoolean(self.bar[2])
        rs = RadioSetting('bar[2]', 'bar value 2', val)
        rs.set_doc('This is test for bar[2]')
        menu.append(rs)

        rs = RadioSettingSubGroup('second part', 'Second Part')
        menu.append(rs)
        print(f'gs1: With SubGrouop: {menu}')
        return menu

    def gs2(self) -> RadioSettingGroup:
        """ returns RadioSettingGroup for second batch.
            This should have tooltips
            """
        menu = RadioSettingGroup('gs2', 'Group 2')
        val = RadioSettingValueBoolean(self.foo[0])
        rs = RadioSetting('Foo[0]', 'Foo value 0', val)
        rs.set_doc('This is test for foo[0]')
        menu.append(rs)
        val = RadioSettingValueBoolean(self.foo[1])
        rs = RadioSetting('Foo[1]', 'Foo value 1', val)
        rs.set_doc('This is test for foo[1]')
        menu.append(rs)
        val = RadioSettingValueBoolean(self.foo[2])
        rs = RadioSetting('Foo[2]', 'Foo value 2', val)
        rs.set_doc('This is test for foo[2]')
        menu.append(rs)
        print(f'gs2: WithOUT SubGrouop: {menu}')
        return menu

    def gs3(self) -> RadioSettingGroup:
        """ returns RadioSettingGroup for third batch """
        menu = RadioSettingGroup('gs3', 'Group 3')
        menu.set_doc('This is settings-group 3 of three total.')
        return menu

    def get_settings(self) -> RadioSettings:
        self.foo = [True, False, True]
        self.bar = [True, True, False, False]
        return RadioSettings(self.gs1(), self.gs2(), self.gs3())
