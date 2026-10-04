import ctypes
from ctypes import wintypes
import logging
import os
import shutil
import subprocess
import psutil
from utils.process import CREATE_FLAGS

log = logging.getLogger(__name__)
GB = 1024 ** 3


class WindowsGPU:
    """Windows performance counters: actual engine activity and dedicated allocations."""
    def __init__(self):
        self.dll = ctypes.WinDLL('pdh')
        self.query = ctypes.c_void_p()
        self.counters = {}
        self.dll.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self.dll.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self.dll.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self.dll.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        self.dll.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        if self.dll.PdhOpenQueryW(None, 0, ctypes.byref(self.query)):
            raise OSError('Windows GPU counters unavailable')
        for name, path in [('gpu', r'\GPU Engine(*)\Utilization Percentage'), ('vram', r'\GPU Adapter Memory(*)\Dedicated Usage')]:
            counter = ctypes.c_void_p()
            if self.dll.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter)) == 0:
                self.counters[name] = counter
        self.dll.PdhCollectQueryData(self.query)

    def values(self, counter):
        class ValueUnion(ctypes.Union):
            _fields_ = [('longValue', ctypes.c_long), ('doubleValue', ctypes.c_double), ('largeValue', ctypes.c_longlong), ('AnsiStringValue', ctypes.c_char_p)]
        class Value(ctypes.Structure):
            _fields_ = [('CStatus', wintypes.DWORD), ('value', ValueUnion)]
        class Item(ctypes.Structure):
            _fields_ = [('szName', wintypes.LPWSTR), ('FmtValue', Value)]
        size, count = wintypes.DWORD(), wintypes.DWORD()
        status = self.dll.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(size), ctypes.byref(count), None)
        if status not in (0, -2147481646, 0x800007D2) or not size.value:
            return []
        buffer = ctypes.create_string_buffer(size.value)
        if self.dll.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(size), ctypes.byref(count), buffer):
            return []
        items = ctypes.cast(buffer, ctypes.POINTER(Item))
        return [(items[i].szName, items[i].FmtValue.value.doubleValue) for i in range(count.value)
                if items[i].FmtValue.CStatus in (0, 1)]

    def sample(self):
        self.dll.PdhCollectQueryData(self.query)
        usage = self.values(self.counters['gpu']) if 'gpu' in self.counters else []
        memory = self.values(self.counters['vram']) if 'vram' in self.counters else []
        # Group per adapter and engine; percentages cannot be added across engines.
        engines = {}
        for name, value in usage:
            key = name.split('_luid_', 1)[-1]
            engines[key] = engines.get(key, 0) + value
        return {'gpu': min(100, max(engines.values())) if engines else None,
                'vram_used': max(v for _, v in memory) if memory else None,
                'gpu_scope': 'Windows busiest GPU engine / largest adapter allocation'}

    def close(self):
        if self.query:
            self.dll.PdhCloseQuery(self.query)
            self.query = None


class ResourceMonitor:
    def __init__(self):
        self.windows = None
        if os.name == 'nt':
            try:
                self.windows = WindowsGPU()
            except (OSError, AttributeError):
                log.info('Windows GPU performance counters are unavailable', exc_info=True)
        psutil.cpu_percent()

    def sample(self):
        memory = psutil.virtual_memory()
        result = dict(cpu=psutil.cpu_percent(), ram_used=memory.used, ram_total=memory.total,
                      app_ram=psutil.Process().memory_info().rss, gpu=None, vram_used=None, temperature=None)
        if self.windows:
            try:
                result.update(self.windows.sample())
            except Exception:
                log.exception('GPU counters failed; disabling counter monitor')
                self.windows.close()
                self.windows = None
        nvidia = shutil.which('nvidia-smi')
        if nvidia:
            try:
                proc = subprocess.run([nvidia, '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                                       '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2, creationflags=CREATE_FLAGS)
                if proc.returncode == 0 and proc.stdout.strip():
                    name, gpu, used, total, temperature = proc.stdout.splitlines()[0].split(',')
                    result.update(gpu=float(gpu), vram_used=float(used) * 1024 ** 2, vram_total=float(total) * 1024 ** 2,
                                  temperature=float(temperature), gpu_scope=name.strip())
            except (ValueError, OSError, subprocess.TimeoutExpired):
                log.debug('NVIDIA metrics unavailable', exc_info=True)
        return result

    def close(self):
        if self.windows:
            self.windows.close()
