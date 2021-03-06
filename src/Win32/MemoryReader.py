#
#   MemoryReader.py
#
#   MemoryReader - Remote process memory inspection python module
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
#

from ..Interfaces import MemReaderInterface, MemWriterInterface, ReadError
from .MemReaderBaseWin import *
from ..GUIDisplayBase import *

from .ProcessCreateAndAttach import *
from .InjectDll import *
from .MemoryMap import *
from .Win32Structs import *

import ctypes
import sys
import struct

def attach(targetProcessId):
    return MemoryReader(target_process_id=targetProcessId)

def createProcess(targetExe):
    return MemoryReader(cmd_line=targetExe)

def createProcessWithInjectedDll(targetExe, targetDll):
    return MemoryReader(cmd_line=targetExe, create_info={"WITH_DLL":targetDll})

def memoryReaderFromHandle(handle):
    return MemoryReader(target_open_handle=handle)

class MemoryReader( MemReaderBaseWin, MemWriterInterface, GUIDisplayBase, InjectDll, ProcessCreateAndAttach ):
    READ_ATTRIBUTES_MASK    = 0xee # [0x20, 0x40, 0x80, 0x02, 0x04, 0x08]
    WRITE_ATTRIBUTES_MASK   = 0xcc # [0x40, 0x80, 0x04, 0x08]
    EXECUTE_ATTRIBUTES_MASK = 0xf0 # [0x10, 0x20, 0x40, 0x80]
    ALL_ATTRIBUTES_MASK     = 0xff
    def __init__( self, \
            target_process_id=None, \
            target_open_handle=None, \
            cmd_line=None, \
            create_suspended=False, \
            create_info=None ):
        MemReaderBaseWin.__init__(self)
        self.REQUIRED_ACCESS = \
                win32con.PROCESS_CREATE_THREAD | \
                win32con.PROCESS_QUERY_INFORMATION | \
                win32con.PROCESS_SET_INFORMATION | \
                win32con.PROCESS_VM_READ | \
                win32con.PROCESS_VM_WRITE | \
                win32con.PROCESS_VM_OPERATION
        ProcessCreateAndAttach.__init__(self,
                target_process_id,
                target_open_handle,
                cmd_line,
                create_suspended,
                create_info )

        for name, (dataSize, packer) in MemReaderInterface.READER_DESC.items():
            def readerCreator(dataSize, name):
                ctype_container = getattr(ctypes, 'c_' + name.lower())
                def readerMethod(self, addr):
                    result = ctype_container(0)
                    bytes_read = c_size_t(0)
                    read_result = ReadProcessMemory( self._process, addr, byref(result), dataSize, byref(bytes_read))
                    if 0 == read_result:
                        raise ReadError(addr)
                    return int(result.value)
                return readerMethod
            def writerCreator(dataSize, packer, name):
                ctype_container = getattr(ctypes, 'c_' + name.lower())
                def writerMethod(self, addr, value):
                    if isinstance(value, integer_types):
                        value = struct.pack(self._ENDIANITY + packer, value)
                    data_to_write = c_buffer(value, dataSize)
                    bytes_written = c_size_t(0)
                    WriteProcessMemory( self._process, addr, data_to_write, dataSize, byref(bytes_written) )
                return writerMethod
            def deprotectAndWriterCreator(dataSize, name):
                def deprotectAndWriterMethod(self, addr, value):
                    originalAttributes = self.deprotectMem(addr, dataSize)
                    getattr(self, 'write' + name)(addr, value)
                    self.setMemProtection(addr, dataSize, originalAttributes)
                return deprotectAndWriterMethod

            setattr(MemoryReader, 'read' + name, readerCreator(dataSize, name))
            setattr(MemoryReader, 'write' + name, writerCreator(dataSize, packer, name))
            setattr(MemoryReader, 'deprotectAndWrite' + name, deprotectAndWriterCreator(dataSize, name))

    def __del__( self ):
        self._closeProcess()

    def deprotectMem( self, addr, size ):
        return self.setMemProtection(addr, size, win32con.PAGE_EXECUTE_READWRITE)

    def setMemProtection( self, addr, size, protection ):
        old_protection = c_uint32(0)
        VirtualProtectEx( self._process, addr, size, protection, byref(old_protection) )
        return old_protection

    def readAddr( self, addr ):
        result = c_void_p(0)
        bytes_read = c_size_t(0)
        read_result = ReadProcessMemory( self._process, addr, byref(result), self._POINTER_SIZE, byref(bytes_read) )
        if 0 == read_result:
            raise ReadError(addr)
        if None == result.value:
            return 0
        return result.value

    def readMemory( self, addr, length ):
        result = c_ARRAY(c_char, length)(b'\x00')
        bytes_read = c_size_t(0)
        read_result = ReadProcessMemory( self._process, addr, byref(result), sizeof(result), byref(bytes_read) )
        if 0 == read_result:
            raise ReadError(addr)
        return result.raw

    def readString( self, addr, maxSize=None, isUnicode=False ):
        result = ''
        bytes_read = c_size_t(0)
        char = c_uint32(0)
        bytesCounter = 0

        while True:
            if False == isUnicode:
                try:
                    ReadProcessMemory( self._process, addr + bytesCounter, byref(char), 1, byref(bytes_read) )
                except WindowsError:
                    return result
                bytesCounter += 1
            else:
                try:
                    ReadProcessMemory( self._process, addr + bytesCounter, byref(char), 2, byref(bytes_read) )
                except WindowsError:
                    return result
                bytesCounter += 2
            if 1 < char.value and char.value < 0x80:
                result += chr(char.value)
            else:
                return result
            if None != maxSize and bytesCounter > maxSize:
                return result

    def writeAddr( self, addr, data ):
        if 4 == self._POINTER_SIZE:
            self.writeUInt32(addr, data)
        elif 8 == self._POINTER_SIZE:
            self.writeUInt64(addr, data)
        else:
            raise Exception("Unknown pointer size")

    def writeMemory( self, addr, data ):
        data_to_write = c_buffer(data)
        bytes_written = c_size_t(0)
        WriteProcessMemory( self._process, addr, data_to_write, len(data), byref(bytes_written) )
        return bytes_written.value

    def deprotectAndWriteMemory( self, addr, data ):
        originalAttributes = self.deprotectMem(addr, len(data))
        self.writeMemory(addr, data)
        self.setMemProtection(addr, len(data), originalAttributes)

    def getAddressAttributes(self, addr):
        memBasicInfo = MEMORY_BASIC_INFORMATION()
        read_result = VirtualQueryEx(
                            self._process,
                            addr,
                            byref(memBasicInfo),
                            sizeof(MEMORY_BASIC_INFORMATION))
        if 0 == read_result:
            raise Exception("Failed to query memory attributes for address 0x%x" % i)
        return (memBasicInfo.Protect)

    def isAddressValid( self, addr ):
        if addr <= self._minVAddress or addr > self._maxVAddress:
            return False
        result = c_uint32(0)
        bytes_read = c_size_t(0)
        returncode = ReadProcessMemory( self._process, addr, byref(result), 1, byref(bytes_read) )
        if 0 != returncode and 1 == bytes_read.value:
            return True
        return False

    def _makeRegionsList(self, pages, names={}):
        result = {}
        pages.sort()

        pageSize = self._pageSize
        pageMask = ~self._pageSizeMask
        regionSize = pageSize
        regionStart = pages[0] & pageMask
        regionProtection = pages[0] & 0x1f
        for i in range(1, len(pages)):
            page = pages[i]
            addr = page & pageMask
            protection = page & 0x1f
            if addr == (regionStart + regionSize) and protection == regionProtection:
                regionSize += pageSize
            else:
                if regionStart in result:
                    raise Exception("Double definition of region! %x" % page)
                name = names.get(regionStart, '')
                result[regionStart] = (name, regionSize, regionProtection)
                regionSize = pageSize
                regionStart = addr
                regionProtection = protection
        name = names.get(regionStart, '')
        result[regionStart] = (name, regionSize, regionProtection)
        return result

    def getMemoryMap(self):
        """ Get map of all the memory with names of the modules the memory belongs to
            Result is of type: {addr: (name, size, attributes)} """

        # Gather information about loaded images
        modules = dict([(x[0], x[1]) for x in self.enumModules()])

        # Find the number of pages in the workingset
        num_pages = c_uint64(0)
        QueryWorkingSet(self._process, byref(num_pages), sizeof(c_uint64))

        # Get all other memory and attributes of pages
        memory_map = c_ARRAY( c_void_p, (num_pages.value + 0x100) * sizeof(c_void_p) )(0)
        QueryWorkingSet( self._process, byref(memory_map), sizeof(memory_map) )
        number_of_pages = memory_map[0]
        memoryRegionInfo = MEMORY_BASIC_INFORMATION()
        pages = memory_map[1:number_of_pages]
        return self._makeRegionsList(pages, modules)

    @staticmethod
    def isInListChecker(target):
        def _isInListChecker(x):
            return x in target
        return _isInListChecker
    @staticmethod
    def isInRangeChecker(target):
        def _isInRangeChecker(x):
            return ((x >= target[0]) and (x < target[1]))
        return _isInRangeChecker
    @staticmethod
    def isEqChecker(target):
        def _isEqChecker(x):
            return x == target
        return _isEqChecker
    @staticmethod
    def stringCaseInsensetiveCmp(target):
        def _stringCaseInsensetiveCmp(x, r):
            return x.replace(b'\x00', b'').lower() == target.lower()
        return _stringCaseInsensetiveCmp

    def search(self, target, ranges=None, targetLength=None, alignment=None, isVerbose=False):
        if sys.version_info < (3,):
            integerTypes = (int, long, tuple, list)
        else:
            integerTypes = (int, tuple, list)
        if None == ranges:
            if isVerbose:
                print("Creating memory map")
            memMap = self.getMemoryMap()
            ranges = [(x[0], x[0] + x[1][1]) for x in memMap.items() if 0 != (x[1][2] & self.WRITE_ATTRIBUTES_MASK)]
            if isVerbose:
                print("Creating memory map - Done")
        if isinstance(ranges, tuple):
            ranges = [ranges]
        if None==alignment:
            if isinstance(target, integerTypes):
                alignment = self._DEFAULT_DATA_SIZE
            else:
                alignment = 1
        if None==targetLength:
            if isinstance(target, integerTypes):
                targetLength = self._DEFAULT_DATA_SIZE
            else:
                targetLength = len(target)
        if isinstance(target, integerTypes):
            if 4 == targetLength:
                targetReader = self.readUInt32
            elif 8 == targetLength:
                targetReader = self.readUInt64
            elif 1 == targetLength:
                targetReader = self.readUInt8
            elif 2 == targetLength:
                targetReader = self.readUInt16
            else:
                raise Exception("Target length %s not supported with integer target" % repr(targetLength))
        else:
            targetReader = lambda addr: self.readMemory(addr, targetLength)

        if isinstance(target, list):
            targetValidator = MemoryReader.isInListChecker(target)
        elif isinstance(target, tuple):
            targetValidator = MemoryReader.isInRangeChecker(target)
        elif isinstance(target, integer_types):
            targetValidator = MemoryReader.isEqChecker(target)
        elif isinstance(target, str):
            targetValidator = MemoryReader.isEqChecker(target)
        else:
            targetValidator = target

        for r in ranges:
            if (r[1] - r[0] - targetLength) <= 0:
                continue
            if isVerbose:
                print("Searching in range: 0x%x to 0x%x" % (r[0], r[1]))
            addr = r[0]
            rangeEnd = r[1]
            try:
                while addr < rangeEnd:
                    if targetValidator(targetReader(addr)):
                        yield addr
                    addr += alignment
            except ReadError:
                if isVerbose:
                    print("Range 0x%x to 0x%x stopped after address 0x%x" % (r[0], r[1], addr))

