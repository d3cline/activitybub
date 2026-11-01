"""Limits and Linux syscall policy for disposable, untrusted-image workers."""
import ctypes
import errno
import os
import platform
import sys

MAX_INPUT = 5 * 1024 * 1024
MAX_SOURCE_PIXELS = 16_000_000
MAX_EDGE = 1600
MAX_OUTPUT_PIXELS = 1200 * 1600
MAX_OUTPUT = 12 + MAX_OUTPUT_PIXELS * 4
WALL_SECONDS = 6

# Native syscall numbers from Linux asm/unistd_64.h and asm-generic/unistd.h.
# No file opens, sockets, process creation, exec, ptrace, signal delivery,
# descriptor transfer, or privilege-changing calls are allowed after setup.
ARCHITECTURES = {
    "x86_64": (0xC000003E, (0,1,3,5,8,9,10,11,12,13,14,15,19,20,24,25,28,35,39,60,63,99,131,158,186,202,219,228,230,231,318)),
    "aarch64": (0xC00000B7, (57,62,63,64,65,66,80,93,94,98,101,113,115,124,128,132,134,135,139,160,172,178,179,214,215,216,222,226,233,278)),
}


def supported():
    return sys.platform == "linux" and platform.machine().lower() in ARCHITECTURES


def restrict_worker():
    """Apply before reading any remote bytes; failure must prevent decoding."""
    if not supported():
        raise OSError("Restricted image workers require Linux x86-64 or arm64")
    import resource
    for limit, values in (
        (resource.RLIMIT_CORE, (0,0)),
        (resource.RLIMIT_FSIZE, (0,0)),
        (resource.RLIMIT_CPU, (3,3)),
        (resource.RLIMIT_AS, (512*1024*1024,512*1024*1024)),
        (resource.RLIMIT_NOFILE, (16,16)),
    ):
        resource.setrlimit(limit,values)
    os.umask(0o077)
    limits={"cpu":resource.getrlimit(resource.RLIMIT_CPU),"memory":resource.getrlimit(resource.RLIMIT_AS),"core":resource.getrlimit(resource.RLIMIT_CORE)}
    os.closerange(3,1_048_576)
    libc = ctypes.CDLL(None,use_errno=True)
    libc.prctl.restype = ctypes.c_int
    arch, calls = ARCHITECTURES[platform.machine().lower()]
    class Filter(ctypes.Structure):
        _fields_ = [("code",ctypes.c_ushort),("jt",ctypes.c_ubyte),("jf",ctypes.c_ubyte),("k",ctypes.c_uint32)]
    class Program(ctypes.Structure):
        _fields_ = [("len",ctypes.c_ushort),("filter",ctypes.POINTER(Filter))]
    # Validate the syscall ABI first, including rejecting x32 syscall numbers.
    instructions = [(0x20,0,0,4),(0x15,1,0,arch),(0x06,0,0,0x80000000),(0x20,0,0,0)]
    for call in calls:
        instructions += [(0x15,0,1,call),(0x06,0,0,0x7fff0000)]
    instructions.append((0x06,0,0,0x00050000 | errno.EPERM))
    filters = (Filter*len(instructions))(*(Filter(*i) for i in instructions))
    program = Program(len(filters),filters)
    if libc.prctl(38,1,0,0,0) != 0 or libc.prctl(22,2,ctypes.byref(program),0,0) != 0:
        raise OSError(ctypes.get_errno(),"Image syscall restrictions unavailable")
    return limits
