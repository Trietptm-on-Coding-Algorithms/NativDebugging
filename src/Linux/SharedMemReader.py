#
#   SharedMemReader.py
#
#   SharedMemReader - Attach and read shared memory on *nix platforms
#
#   https://github.com/assafnativ/NativDebugging.git
#   Nativ.Assaf@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>

import sys
import struct
from ctypes import c_char, c_void_p, c_int8, c_int16, c_int32, c_int64, c_uint8, c_uint16, c_uint32, c_uint64, cdll, sizeof
import subprocess
from subprocess import Popen

from ..Interfaces import MemReaderInterface, ReadError
from ..MemReaderBase import *
from ..GUIDisplayBase import *
from ..Utilities import *
try:
    from ..QtWidgets import *
    IS_GUI_FOUND = True
except ImportError as e:
    #print("No GUI support")
    IS_GUI_FOUND = False

class SharedMemInfo(object):
    def __init__(self, id, localAddress, base, size):
        self.id = id
        self.localAddress = localAddress
        self.localAddressEnd = localAddress + size
        self.end = base + size
        self.size = size
        self.base = base
        self.delta = self.localAddress - base
    def __repr__(self):
        return "MemInfo:Id0x%x:Base0x%x:End0x%x:LocalAddress0x%x" % (self.id, self.base, self.end, self.localAddress)

def attach(memInfo):
    # memInfo: (memId, baseAddress, size)
    return SharedMemReader(memInfo)

class SharedMemReader( MemReaderBase, GUIDisplayBase ):
    def __init__(self, memInfos):
        MemReaderBase.__init__(self)
        self._POINTER_SIZE = sizeof(c_void_p)
        self._DEFAULT_DATA_SIZE = 4
        t = pack('=L', 1)
        if '\x00' == t[0]:
            self._ENDIANITY = '>'
        else:
            self._ENDIANITY = '<'
        libc = cdll.LoadLibrary("libc.so.6")
        self.shmat = libc.shmat
        self.shmat.argv = [c_uint32, c_void_p, c_uint32]
        self.shmat.restype = c_void_p
        self.shmdt = libc.shmdt
        self.shmdt.argv = [c_void_p]
        self.shmdt.restype = None
        # Support more than one shmid on input
        if not isinstance(memInfos, list):
            memInfos = [memInfos]
        for memInfo in memInfos:
            if 3 != len(memInfo) or tuple != type(memInfo):
                raise Exception("Meminfo of type (shared mem id, base address, size in bytes) expected")
        self.memMap = []
        for memInfo in memInfos:
            mem = self.shmat(memInfo[0], 0, 0o10000) # 010000 == SHM_RDONLY
            if c_void_p(-1).value == mem or None == mem:
                raise Exception("Attach to shared memory failed")
            self.memMap.append(SharedMemInfo(memInfo[0], mem, memInfo[1], memInfo[2]))


        for name, (dataSize, packer) in MemReaderInterface.READER_DESC.items():
            def readerCreator(dataSize, name):
                ctype_container = getattr(ctypes, 'c_' + name.lower())
                def readerMethod(self, address):
                    address = self.remoteAddressToLocalAddress(address)
                    return int(ctype_container.from_address(address).value)
                return readerMethod
            def localRederCreator(dataSize, name):
                ctype_container = getattr(ctypes, 'c_' + name.lower())
                def readerMethod(self, address):
                    return int(ctype_container.from_address(address).value)
                return readerMethod
            setattr(SharedMemReader, 'read' + name, readerCreator(dataSize, name))
            setattr(SharedMemReader, 'readLocal' + name, localReaderCreator(dataSize, name))

    def remoteAddressToLocalAddress(self, address):
        for mem in self.memMap:
            if address >= mem.base and address < mem.end:
                return address + mem.delta
        raise ReadError(address)

    def __del__(self):
        self.__detach()

    def detach(self):
        self.__detach()
        del(self)

    def __detach(self):
        for mem in self.memMap:
            self.shmdt(mem.localAddress)
        self.memMap = []

    def getMemoryMap(self):
        memMap = {}
        for mem in self.memMap:
            memMap[mem.base] = ('%d' % mem.id, mem.size, 0xffffffff)
        return memMap

    def readMemory(self, address, length, isLocalAddress=False):
        if not isLocalAddress:
            address = self.remoteAddressToLocalAddress(address)
        val = (c_char * length).from_address(address)
        return val.raw

    def readAddr(self, address, isLocalAddress=False):
        if not isLocalAddress:
            address = self.remoteAddressToLocalAddress(address)
        if 4 == self._POINTER_SIZE:
            return c_uint32.from_address(address).value
        else:
            return c_uint64.from_address(address).value

    def isAddressValid(self, address, isLocalAddress=False):
        if not isLocalAddress:
            for mem in self.memMap:
                if address >= mem.base and address < mem.end:
                    return True
        else:
            for mem in self.memMap:
                if address >= mem.localAddress and address < mem.localAddressEnd:
                    return True
        return False

    def readString( self, addr, isLocalAddress=False, maxSize=None, isUnicode=False ):
        result = ''
        bytesCounter = 0

        while True:
            if False == isUnicode:
                c = self.readUInt8(addr + bytesCounter, isLocalAddress=isLocalAddress)
                bytesCounter += 1
            else:
                c = self.readUInt16(addr + bytesCounter, isLocalAddress=isLocalAddress)
                bytesCounter += 2
            if 1 < c and c < 0x80:
                result += chr(c)
            else:
                return result
            if None != maxSize and bytesCounter > maxSize:
                return result

