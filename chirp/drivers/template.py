# Copyright 2012 Dan Smith <dsmith@danplanet.com>
# Copyright 2025 Declan Rieb <wd5eqy@arrl.net>
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

""" This copy of the CHIRP template driver 'template' is designed to test special
    memories that can be accessed via bank mappings.
    I've been trying to do that with ft1d.py without great success, so this
    is a toy version.

    It requires that CHIRP have the Dan's 'banks_with_specials' commit.
"""
from chirp import chirp_common, memmap
from chirp import bitwise, directory

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
struct {
    char name[16];
    u16  channels[10];
} bank[5]; 
"""

YAESU_PRESETS = {
     'SWL1': (0x1000, 'VOA', 6030000, 'AM', '', 0, 'USA'),
     'SWL2': (0x1001, 'VOA', 6160000, 'AM', '', 0, 'USA'),
     'SWL3': (0x1002, 'VOA', 9760000, 'AM', '', 0, 'USA'),
     'SWL4': (0x1003, 'VOA', 11965000, 'AM', '', 0, 'USA'),
     'SWL5': (0x1004, 'Canada', 9555000, 'AM', '', 0, ''),
     'WX1': (0x4000, 'WX1PA7', 162550000, 'FM', '', 0, ''),
     'WX3': (0x4001, 'WX2PA1', 162400000, 'FM', '', 0, ''),
    }

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

class TemplateBankModel(chirp_common.BankModel,
                        chirp_common.SpecialBankModelInterface):
    ''' Copied from fake.py driver, in the FakeBankModel object.
        This implementation requires NO interaction with radio data.
        '''

    def get_bankable_specials(self) -> list:
        return list(YAESU_PRESETS.keys())

    def __init__(self, radio, name='Banks'):
        super().__init__(radio, name)
        self._banks = {i: set() for i in range(5)}

    def get_num_mappings(self):
        return len(self._banks)

    def get_mappings(self):
        return [chirp_common.Bank(self, i, 'Bank %i' % i) for i in range(5)]

    def get_memory_mappings(self, memory):
        banks = self.get_mappings()
        in_banks = [i for i in range(5) if memory.number in self._banks[i]]
        return [bank for bank in banks if bank.get_index() in in_banks]

    def add_memory_to_mapping(self, memory, mapping):
        self._banks[mapping.get_index()].add(memory.number)

    def remove_memory_from_mapping(self, memory, mapping):
        self._banks[mapping.get_index()].remove(memory.number)


# Uncomment this to actually register this radio in CHIRP
@directory.register
class TemplateRadio(chirp_common.CloneModeRadio):
    """Acme Template"""
    VENDOR = "Acme"     # Replace this with your vendor
    MODEL = "Template"  # Replace this with your model
    BAUD_RATE = 9600    # Replace this with your baud rate

    # All new drivers should be "Byte Clean" so leave this in place.

    def __init__(self, pipe) -> None:
        super().__init__(pipe)
        self._oldnum = -1

    def get_bank_model(self) -> chirp_common.BankModel:
        ''' Returns an appropriate list of MappingModel objects '''
        return TemplateBankModel(self)

    # Return information about this radio's features, including
    # how many memories it has, what bands it supports, etc
    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_bank = True
        rf.memory_bounds = (0, 9)  # This radio supports memories 0-9
        rf.valid_bands = [(144000000, 148000000),  # Supports 2-meters
                          (440000000, 450000000),  # Supports 70-centimeters
                          ]
        rf.valid_special_chans = list(YAESU_PRESETS.keys())
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
    def get_raw_memory(self, number):
        return repr(self._memobj.memory[number])

    # Extract a high-level memory object from the low-level memory map
    # This is called to populate a memory in the UI
    def get_memory(self, number: int | str) -> chirp_common.Memory:
        # Create a high-level memory object to return to the UI
        mem = chirp_common.Memory()
        if isinstance(number, str):
            mem.empty = False
            mem.freq = YAESU_PRESETS[number][2]
            mem.name = YAESU_PRESETS[number][1]
            mem.mode = YAESU_PRESETS[number][3]
            mem.comment = YAESU_PRESETS[number][6]
            self._oldnum += 1
            mem.number = self._oldnum
            mem.extd_number = number
            mem.immutable = ['freq', 'name', 'number', 'extd_number',
                             'comment', 'empty', 'skip']
            return mem
        # number is a number    
        mem.number = number                 # Set the memory number
        self._oldnum = max(self._oldnum, number)
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[number]

        # Convert your low-level frequency to Hertz
        mem.freq = int(_mem.freq)
        mem.name = str(_mem.name).rstrip()  # Set the alpha tag

        # We'll consider any blank (i.e. 0 MHz frequency) to be empty
        if mem.freq == 0:
            mem.empty = True

        return mem

    # Store details about a high-level memory to the memory map
    # This is called when a user edits a memory in the UI
    def set_memory(self, mem):
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[mem.number]

        # Convert to low-level frequency representation
        _mem.freq = mem.freq
        _mem.name = mem.name.ljust(8)[:8]  # Store the alpha tag
