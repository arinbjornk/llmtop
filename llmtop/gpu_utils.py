"""
Apple Silicon GPU monitoring utilities.
Provides real-time GPU usage without requiring sudo privileges.
Uses ioreg (preferred) or IOReport framework.
"""

import ctypes
import ctypes.util
import time
import platform
import os
import logging
import subprocess
import re
from typing import Optional, Dict

# Module-level state
_ioreport_available: Optional[bool] = None
_frameworks_loaded = False
_IOKit = None
_CoreFoundation = None
_IOReport = None
_kCFAllocatorDefault = None

# Type definitions (will be set after framework load)
CFTypeRef = ctypes.c_void_p
CFStringRef = ctypes.c_void_p
CFDictionaryRef = ctypes.c_void_p
CFArrayRef = ctypes.c_void_p
CFAllocatorRef = ctypes.c_void_p
CFIndex = ctypes.c_long
CFStringEncoding = ctypes.c_uint32
kCFStringEncodingUTF8 = 0x08000100


def _load_frameworks() -> bool:
    """Load macOS frameworks required for IOReport. Returns True if successful."""
    global _frameworks_loaded, _IOKit, _CoreFoundation, _IOReport, _kCFAllocatorDefault

    if _frameworks_loaded:
        return True

    # Only works on macOS ARM
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        logging.debug("IOReport: Not on Apple Silicon Mac, skipping")
        return False

    try:
        # Find and load libraries
        iokit_path = ctypes.util.find_library("IOKit")
        corefoundation_path = ctypes.util.find_library("CoreFoundation")

        # Try multiple possible paths for IOReport private framework
        ioreport_paths = [
            "/System/Library/PrivateFrameworks/IOReport.framework/Versions/A/IOReport",
            "/System/Library/PrivateFrameworks/IOReport.framework/IOReport",
            "/usr/lib/libIOReport.dylib",
        ]

        ioreport_path = None
        for path in ioreport_paths:
            if os.path.exists(path):
                ioreport_path = path
                break

        if not iokit_path or not corefoundation_path:
            logging.debug("IOReport: Could not find IOKit or CoreFoundation")
            return False

        if not ioreport_path:
            logging.debug("IOReport: Private framework not found at any expected path")
            return False

        _IOKit = ctypes.CDLL(iokit_path, use_errno=True)
        _CoreFoundation = ctypes.CDLL(corefoundation_path, use_errno=True)
        _IOReport = ctypes.CDLL(ioreport_path, use_errno=True)

        # Get kCFAllocatorDefault
        _kCFAllocatorDefault = CFAllocatorRef.in_dll(_CoreFoundation, 'kCFAllocatorDefault')

        # Set up CoreFoundation function signatures
        _CoreFoundation.CFStringCreateWithCString.argtypes = [CFAllocatorRef, ctypes.c_char_p, CFStringEncoding]
        _CoreFoundation.CFStringCreateWithCString.restype = CFStringRef

        _CoreFoundation.CFStringGetLength.argtypes = [CFStringRef]
        _CoreFoundation.CFStringGetLength.restype = CFIndex

        _CoreFoundation.CFStringGetMaximumSizeForEncoding.argtypes = [CFIndex, CFStringEncoding]
        _CoreFoundation.CFStringGetMaximumSizeForEncoding.restype = CFIndex

        _CoreFoundation.CFStringGetCString.argtypes = [CFStringRef, ctypes.c_char_p, CFIndex, CFStringEncoding]
        _CoreFoundation.CFStringGetCString.restype = ctypes.c_bool

        _CoreFoundation.CFDictionaryGetValue.argtypes = [CFDictionaryRef, CFStringRef]
        _CoreFoundation.CFDictionaryGetValue.restype = ctypes.c_void_p

        _CoreFoundation.CFRelease.argtypes = [CFTypeRef]
        _CoreFoundation.CFRelease.restype = None

        _CoreFoundation.CFArrayGetCount.argtypes = [CFArrayRef]
        _CoreFoundation.CFArrayGetCount.restype = CFIndex

        _CoreFoundation.CFArrayGetValueAtIndex.argtypes = [CFArrayRef, CFIndex]
        _CoreFoundation.CFArrayGetValueAtIndex.restype = ctypes.c_void_p

        # Set up IOReport function signatures
        IOReportSubscriptionRef = ctypes.c_void_p
        CFMutableDictionaryRef = ctypes.c_void_p

        _IOReport.IOReportCopyChannelsInGroup.argtypes = [CFStringRef, CFStringRef, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64]
        _IOReport.IOReportCopyChannelsInGroup.restype = CFDictionaryRef

        _IOReport.IOReportCreateSubscription.argtypes = [CFTypeRef, CFMutableDictionaryRef, ctypes.POINTER(CFMutableDictionaryRef), ctypes.c_uint64, CFTypeRef]
        _IOReport.IOReportCreateSubscription.restype = IOReportSubscriptionRef

        _IOReport.IOReportCreateSamples.argtypes = [IOReportSubscriptionRef, CFDictionaryRef, CFTypeRef]
        _IOReport.IOReportCreateSamples.restype = CFDictionaryRef

        _IOReport.IOReportCreateSamplesDelta.argtypes = [CFDictionaryRef, CFDictionaryRef, CFTypeRef]
        _IOReport.IOReportCreateSamplesDelta.restype = CFDictionaryRef

        _IOReport.IOReportStateGetCount.argtypes = [CFDictionaryRef]
        _IOReport.IOReportStateGetCount.restype = ctypes.c_int32

        _IOReport.IOReportStateGetNameForIndex.argtypes = [CFDictionaryRef, ctypes.c_int32]
        _IOReport.IOReportStateGetNameForIndex.restype = CFStringRef

        _IOReport.IOReportStateGetResidency.argtypes = [CFDictionaryRef, ctypes.c_int32]
        _IOReport.IOReportStateGetResidency.restype = ctypes.c_uint64

        _frameworks_loaded = True
        logging.info("IOReport: Frameworks loaded successfully")
        return True

    except Exception as e:
        logging.debug(f"IOReport: Failed to load frameworks: {e}")
        return False


def _cfstring_to_string(cf_string: CFStringRef) -> Optional[str]:
    """Convert a CFStringRef to a Python string."""
    if not cf_string or not _CoreFoundation:
        return None
    try:
        length = _CoreFoundation.CFStringGetLength(cf_string)
        max_size = _CoreFoundation.CFStringGetMaximumSizeForEncoding(length, kCFStringEncodingUTF8)
        buffer = ctypes.create_string_buffer(max_size + 1)
        if _CoreFoundation.CFStringGetCString(cf_string, buffer, max_size + 1, kCFStringEncodingUTF8):
            return buffer.value.decode('utf-8')
    except Exception:
        pass
    return None


def _string_to_cfstring(py_string: str) -> CFStringRef:
    """Convert a Python string to a CFStringRef. Caller must release."""
    return _CoreFoundation.CFStringCreateWithCString(
        _kCFAllocatorDefault, py_string.encode('utf-8'), kCFStringEncodingUTF8
    )


def _cf_release(cf_ref: CFTypeRef):
    """Safely release a CoreFoundation object."""
    if cf_ref and _CoreFoundation:
        _CoreFoundation.CFRelease(cf_ref)


def is_available() -> bool:
    """Check if IOReport GPU monitoring is available on this system."""
    global _ioreport_available

    if _ioreport_available is not None:
        return _ioreport_available

    _ioreport_available = _load_frameworks()
    return _ioreport_available


def get_gpu_usage(sample_duration_ms: int = 100) -> Optional[float]:
    """
    Get Apple Silicon GPU usage percentage using IOReport framework.

    Args:
        sample_duration_ms: Duration between samples in milliseconds (default 100ms)

    Returns:
        GPU usage as a percentage (0-100), or None if unavailable/error
    """
    if not is_available():
        return None

    subscription = None
    channels_dict = None
    cf_group_name = None
    cf_subgroup_name = None
    sample1 = None
    sample2 = None
    delta = None
    cf_key_channels = None
    cf_key_states = None

    try:
        # Define channel group and subgroup for GPU stats
        group_name = "GPU Stats"
        subgroup_name = "GPU Performance States"

        cf_group_name = _string_to_cfstring(group_name)
        cf_subgroup_name = _string_to_cfstring(subgroup_name)

        if not cf_group_name or not cf_subgroup_name:
            return None

        # Get channels dictionary
        channels_dict = _IOReport.IOReportCopyChannelsInGroup(
            cf_group_name, cf_subgroup_name, 0, 0, 0
        )
        if not channels_dict:
            logging.debug("IOReport: Could not get GPU channels")
            return None

        # Create subscription
        CFMutableDictionaryRef = ctypes.c_void_p
        null_dict_ptr = ctypes.POINTER(CFMutableDictionaryRef)()
        subscription = _IOReport.IOReportCreateSubscription(None, channels_dict, null_dict_ptr, 0, None)
        if not subscription:
            return None

        # Take two samples
        sample1 = _IOReport.IOReportCreateSamples(subscription, channels_dict, None)
        if not sample1:
            return None

        time.sleep(sample_duration_ms / 1000.0)

        sample2 = _IOReport.IOReportCreateSamples(subscription, channels_dict, None)
        if not sample2:
            return None

        # Calculate delta
        delta = _IOReport.IOReportCreateSamplesDelta(sample1, sample2, None)
        if not delta:
            return None

        # Extract GPU states from delta
        cf_key_channels = _string_to_cfstring("IOReportChannels")
        channels_array_ref = _CoreFoundation.CFDictionaryGetValue(delta, cf_key_channels)
        if not channels_array_ref:
            return None

        channels_count = _CoreFoundation.CFArrayGetCount(channels_array_ref)
        if channels_count == 0:
            return None

        gpu_channel_dict = _CoreFoundation.CFArrayGetValueAtIndex(channels_array_ref, 0)
        if not gpu_channel_dict:
            return None

        cf_key_states = _string_to_cfstring("IOReportStates")
        gpu_states = _CoreFoundation.CFDictionaryGetValue(gpu_channel_dict, cf_key_states)
        if not gpu_states:
            return None

        # Calculate residency
        state_count = _IOReport.IOReportStateGetCount(gpu_states)
        total_residency = 0
        active_residency = 0

        for i in range(state_count):
            cf_state_name = _IOReport.IOReportStateGetNameForIndex(gpu_states, i)
            residency = _IOReport.IOReportStateGetResidency(gpu_states, i)
            state_name = _cfstring_to_string(cf_state_name)

            if state_name:
                total_residency += residency
                # Active states exclude OFF, IDLE, DOWN
                if state_name not in ("OFF", "IDLE", "DOWN"):
                    active_residency += residency

        if total_residency == 0:
            return 0.0

        usage_percent = (active_residency / total_residency) * 100.0
        return usage_percent

    except Exception as e:
        logging.debug(f"IOReport: Error getting GPU usage: {e}")
        return None

    finally:
        # Cleanup CoreFoundation objects
        _cf_release(subscription)
        _cf_release(channels_dict)
        _cf_release(cf_group_name)
        _cf_release(cf_subgroup_name)
        _cf_release(sample1)
        _cf_release(sample2)
        _cf_release(delta)
        _cf_release(cf_key_channels)
        _cf_release(cf_key_states)


# --- ioreg-based GPU stats (alternative to IOReport) ---

def get_gpu_stats_ioreg() -> Optional[Dict]:
    """
    Get Apple Silicon GPU stats using ioreg command.
    Returns dict with 'usage' (%), 'memory_used' (bytes), or None if unavailable.
    """
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        return None

    try:
        result = subprocess.run(
            ['ioreg', '-r', '-d', '1', '-c', 'IOAccelerator'],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode != 0:
            return None

        output = result.stdout

        stats = {}

        # Extract Device Utilization %
        match = re.search(r'"Device Utilization %"\s*=\s*(\d+)', output)
        if match:
            stats['usage'] = float(match.group(1))

        # Extract In use system memory (GPU memory in use)
        match = re.search(r'"In use system memory"\s*=\s*(\d+)', output)
        if match:
            stats['memory_used'] = int(match.group(1))

        # Extract Alloc system memory (total allocated)
        match = re.search(r'"Alloc system memory"\s*=\s*(\d+)', output)
        if match:
            stats['memory_alloc'] = int(match.group(1))

        return stats if stats else None

    except Exception as e:
        logging.debug(f"ioreg GPU stats failed: {e}")
        return None


def get_gpu_usage_ioreg() -> Optional[float]:
    """Get GPU usage percentage via ioreg. Returns None if unavailable."""
    stats = get_gpu_stats_ioreg()
    return stats.get('usage') if stats else None
