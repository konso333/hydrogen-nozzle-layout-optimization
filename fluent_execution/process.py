"""Structured subprocess invocation with bounded wait and descendant cleanup."""

import ctypes
import os
import signal
import subprocess


class WindowsJob:
    """Kill-on-close job with best-effort launcher/process cleanup.

    Assign immediately after Popen, before waiting. Failure to assign fails the
    run and kills the launcher. A launcher that breaks away during this small
    creation/assignment interval is not supported by this adapter.
    """

    def __init__(self):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.SetInformationJobObject.restype = w.BOOL
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.AssignProcessToJobObject.restype = w.BOOL
        api.CloseHandle.argtypes = [w.HANDLE]
        api.CloseHandle.restype = w.BOOL
        self.api = api
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        settings = Extended()
        settings.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(settings), ctypes.sizeof(settings)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def run_process(argv, *, cwd, stdout, stderr, timeout, on_started):
    """No shell, inherited stdin, pipe deadlocks or unbounded output buffering."""
    process = None
    job = WindowsJob() if os.name == "nt" else None
    try:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if job else {"start_new_session": True}
        process = subprocess.Popen(argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, **options)
        if job:
            job.assign(process)
        on_started()
        return process.wait(timeout=timeout)
    finally:
        # Best-effort cleanup even on success. Full Fluent/MPI descendant
        # containment, including the pre-assignment window, is not verified.
        try:
            if job:
                job.close()
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
