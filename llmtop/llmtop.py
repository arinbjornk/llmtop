import psutil
import platform
import os
import time
from datetime import datetime
import argparse
from collections import deque
from typing import Dict, List, Any, Optional # Added Optional
import logging
import json
import subprocess # Added for Apple Silicon
import sys # For stdout.flush()
import select # For non-blocking keyboard input
import tty
import termios

# Import Apple Silicon GPU utilities (optional)
try:
    from . import gpu_utils
except ImportError:
    try:
        import gpu_utils
    except ImportError:
        gpu_utils = None

# Try importing optional dependencies, but don't fail if not there
try:
    import GPUtil
except ImportError:
    GPUtil = None # Set to None if not installed

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.console import Group
from rich import box

# Set up logging configuration at the top of the file
logging.basicConfig(
    level=logging.INFO, # Changed default level to INFO, DEBUG is very verbose
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='system_monitor.log'
)
console = Console()

class SystemMetricsCollector:
    def __init__(self, history_length: int = 60):
        self.history_length = history_length
        self.history = {
            'cpu': deque(maxlen=history_length),
            'memory': deque(maxlen=history_length),
            'network': deque(maxlen=history_length),
            'disk': deque(maxlen=history_length),
            'processes': deque(maxlen=history_length),
            'gpu': deque(maxlen=history_length) # GPU history confirmed
        }
        self.system_info = self._get_system_info()
        # Cache initial network counters for rate calculation
        self.last_net_io = psutil.net_io_counters()
        self.last_collection_time = time.monotonic()

    def _get_system_info(self) -> Dict:
        uname = platform.uname()
        return {
            "System": uname.system,
            "Node Name": uname.node,
            "Release": uname.release,
            "Version": uname.version,
            "Machine": uname.machine,
            "Processor": uname.processor
        }

    def _get_uptime_string(self) -> str:
        """Get system uptime as a human-readable string."""
        try:
            boot_time = psutil.boot_time()
            uptime_seconds = time.time() - boot_time
            days, remainder = divmod(int(uptime_seconds), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, _ = divmod(remainder, 60)
            if days > 0:
                return f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        except Exception:
            return "N/A"

    def _get_cpu_info(self) -> Dict:
        try:
            # Reduce interval for less CPU overhead caused by monitoring itself
            cpu_percent = psutil.cpu_percent(interval=0.5, percpu=True) # Increased interval slightly
            cpu_freq = psutil.cpu_freq() # Get frequency after percentage calc
            return {
                "Physical Cores": psutil.cpu_count(logical=False),
                "Total Cores": psutil.cpu_count(logical=True),
                "Current Frequency": f"{cpu_freq.current:.1f}MHz" if cpu_freq else "N/A",
                "Max Frequency": f"{cpu_freq.max:.1f}MHz" if cpu_freq else "N/A", # Added Max Frequency
                "Per Core Usage": cpu_percent,
                "Average Usage": sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0.0
            }
        except Exception as e:
            logging.warning(f"Could not retrieve CPU frequency: {e}")
            # Fallback if frequency fails
            cpu_percent = psutil.cpu_percent(interval=0.5, percpu=True)
            return {
                 "Physical Cores": psutil.cpu_count(logical=False),
                "Total Cores": psutil.cpu_count(logical=True),
                "Current Frequency": "N/A",
                "Max Frequency": "N/A",
                "Per Core Usage": cpu_percent,
                "Average Usage": sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0.0
            }

    def _get_apple_silicon_gpu_info(self) -> List[Dict]:
        """Get Apple Silicon GPU info, using IOReport for real-time load when available."""
        gpu_info = []

        # Get static info from system_profiler
        gpu_name = "Apple Silicon GPU"
        vram = "N/A"
        try:
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True, check=True)
            output = result.stdout

            for line in output.splitlines():
                stripped = line.strip()
                # Parse chipset model (e.g., "Chipset Model: Apple M1 Pro")
                if stripped.startswith("Chipset Model:"):
                    gpu_name = stripped.split(":", 1)[1].strip()
                # Parse VRAM info (various formats across macOS versions)
                elif "VRAM" in stripped and ":" in stripped:
                    vram = stripped.split(":", 1)[1].strip()
                # Also check for "Total Number of Cores" as backup GPU identifier
                elif stripped.startswith("Total Number of Cores:") and gpu_name == "Apple Silicon GPU":
                    # We found GPU cores but no name yet, keep looking
                    pass
        except Exception as e:
            logging.debug(f"Could not get Apple Silicon GPU info via system_profiler: {e}")

        # Try to get real-time GPU load (no sudo required)
        gpu_load = "N/A"
        gpu_mem_used = "N/A"
        if gpu_utils:
            try:
                # Try ioreg first (more reliable on recent macOS)
                stats = gpu_utils.get_gpu_stats_ioreg()
                if stats:
                    if 'usage' in stats:
                        gpu_load = f"{stats['usage']:.0f}%"
                    if 'memory_used' in stats:
                        mem_mb = stats['memory_used'] / (1024 * 1024)
                        gpu_mem_used = f"{mem_mb:.0f}MB"
                # Fallback to IOReport if ioreg didn't work
                elif gpu_utils.is_available():
                    usage = gpu_utils.get_gpu_usage(sample_duration_ms=50)
                    if usage is not None:
                        gpu_load = f"{usage:.1f}%"
            except Exception as e:
                logging.debug(f"GPU usage collection failed: {e}")

        gpu_info.append({
            "ID": 0,
            "Name": gpu_name,
            "Load": gpu_load,
            "Memory Used": gpu_mem_used,
            "Memory Total": vram if vram != "N/A" else "Unified",
            "Memory Util": "N/A",  # Not available without powermetrics
            "Temperature": "N/A",  # Not available without powermetrics
            "UUID": "apple-silicon",
            "Type": "Apple"
        })

        return gpu_info

    def _get_gpu_info(self) -> List[Dict]:
        """Get information about available GPUs (NVIDIA or Apple Silicon)."""
        gpu_info = []

        # 1. Try NVIDIA GPUs using GPUtil
        if GPUtil: # Check if GPUtil was imported successfully
            try:
                gpus = GPUtil.getGPUs()
                for i, gpu in enumerate(gpus):
                    gpu_info.append({
                        "ID": gpu.id, # Use gpu.id provided by GPUtil
                        "Name": gpu.name,
                        "Load": f"{gpu.load * 100:.1f}%",
                        "Memory Used": f"{gpu.memoryUsed:.1f}MB",
                        "Memory Total": f"{gpu.memoryTotal:.1f}MB",
                        "Memory Util": f"{gpu.memoryUtil * 100:.1f}%",
                        "Temperature": f"{gpu.temperature}°C" if gpu.temperature is not None else "N/A",
                        "UUID": gpu.uuid,
                        "Type": "NVIDIA"
                    })
                if gpus:
                    logging.debug(f"Found {len(gpus)} NVIDIA GPUs via GPUtil.")
                    return gpu_info # Return immediately if NVIDIA GPUs found
            except Exception as e:
                # Log specific GPUtil errors but continue to check for Apple Silicon
                logging.warning(f"GPUtil error: {str(e)}. Checking for other GPU types.")

        # 2. If no NVIDIA GPUs found OR GPUtil not available, check for Apple Silicon on macOS
        if not gpu_info and platform.system() == 'Darwin' and platform.machine() == 'arm64':
            logging.debug("Checking for Apple Silicon GPU.")
            gpu_info = self._get_apple_silicon_gpu_info()
            if gpu_info:
                 logging.debug("Found Apple Silicon GPU info.")
                 return gpu_info

        # 3. If still no GPU info, return empty list
        if not gpu_info:
            logging.debug("No compatible GPUs detected (NVIDIA or Apple Silicon).")

        return gpu_info


    def _get_memory_info(self) -> Dict:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "Total": f"{vm.total / (1024**3):.1f}GB",
            "Available": f"{vm.available / (1024**3):.1f}GB",
            "Used": f"{vm.used / (1024**3):.1f}GB",
            "Percentage": vm.percent,
            "Swap Total": f"{swap.total / (1024**3):.1f}GB", # Added Swap Total
            "Swap Used": f"{swap.used / (1024**3):.1f}GB",
            "Swap Percentage": swap.percent
        }

    def _get_disk_info(self) -> List[Dict]:
        disk_info = []
        try:
            # Prioritize root mount point if possible
            partitions = psutil.disk_partitions()
            root_partition = next((p for p in partitions if p.mountpoint == '/'), None)

            if root_partition:
                 try:
                     usage = psutil.disk_usage(root_partition.mountpoint)
                     disk_info.append({
                         "Device": root_partition.device,
                         "Mountpoint": root_partition.mountpoint,
                         "Total": f"{usage.total / (1024**3):.1f}GB",
                         "Used": f"{usage.used / (1024**3):.1f}GB",
                         "Free": f"{usage.free / (1024**3):.1f}GB",
                         "Percentage": usage.percent
                     })
                 except (PermissionError, FileNotFoundError) as e:
                     logging.warning(f"Could not get disk usage for root '/': {e}")

            # Add other relevant partitions (physical disks, avoiding snaps, tmpfs etc.)
            for partition in partitions:
                if partition.mountpoint != '/' and partition.fstype and 'tmpfs' not in partition.fstype and '/snap/' not in partition.mountpoint and partition.opts != 'ro': # Example filter
                    try:
                        usage = psutil.disk_usage(partition.mountpoint)
                        # Only add if total size > 0 (filters out some virtual fs)
                        if usage.total > 0:
                             disk_info.append({
                                 "Device": partition.device,
                                 "Mountpoint": partition.mountpoint,
                                 "Total": f"{usage.total / (1024**3):.1f}GB",
                                 "Used": f"{usage.used / (1024**3):.1f}GB",
                                 "Free": f"{usage.free / (1024**3):.1f}GB",
                                 "Percentage": usage.percent
                             })
                    except (PermissionError, FileNotFoundError) as e:
                        logging.debug(f"Skipping disk {partition.mountpoint}: {e}") # Debug level for skips
                        continue
        except Exception as e:
             logging.error(f"Error getting disk partitions: {e}", exc_info=True)

        return disk_info


    def _get_network_info(self) -> Dict:
        current_time = time.monotonic()
        current_net_io = psutil.net_io_counters()
        time_delta = current_time - self.last_collection_time

        if time_delta <= 0: # Avoid division by zero on first run or timer issue
            sent_rate = 0
            recv_rate = 0
        else:
            sent_rate = (current_net_io.bytes_sent - self.last_net_io.bytes_sent) / time_delta
            recv_rate = (current_net_io.bytes_recv - self.last_net_io.bytes_recv) / time_delta

        # Update caches for next calculation
        self.last_net_io = current_net_io
        self.last_collection_time = current_time

        # Function to format bytes/s to Mbps or Kbps
        def format_rate(rate_bytes_per_sec):
            rate_bits_per_sec = rate_bytes_per_sec * 8
            if rate_bits_per_sec >= 1_000_000:
                return f"{rate_bits_per_sec / 1_000_000:.1f} Mbps"
            elif rate_bits_per_sec >= 1_000:
                return f"{rate_bits_per_sec / 1_000:.1f} Kbps"
            else:
                return f"{rate_bits_per_sec:.0f} bps"

        return {
            "Sent Rate": format_rate(sent_rate),
            "Recv Rate": format_rate(recv_rate),
            "Total Sent": f"{current_net_io.bytes_sent / (1024**3):.2f} GB", # Use GB for total
            "Total Received": f"{current_net_io.bytes_recv / (1024**3):.2f} GB",
            "Packets Sent": current_net_io.packets_sent,
            "Packets Received": current_net_io.packets_recv,
            #"Error In": current_net_io.errin, # Less critical, can be noisy
            #"Error Out": current_net_io.errout
        }

    def _get_process_info(self, limit: int = 10) -> List[Dict]:
        processes = []
        try:
            # Fetch process info once
            proc_iter = psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'username'])
            all_procs_info = []
            for proc in proc_iter:
                 try:
                     pinfo = proc.info
                     # cpu_percent might be None on first call, handle it
                     if pinfo['cpu_percent'] is None:
                         pinfo['cpu_percent'] = 0.0 # Assume 0 if None
                     if pinfo['memory_percent'] is None:
                         pinfo['memory_percent'] = 0.0
                     all_procs_info.append(pinfo)
                 except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                     continue
                 except Exception as e:
                    logging.warning(f"Error getting info for a process: {str(e)}")
                    continue

            # Filter out low CPU processes *after* collecting all, and sort
            # Keep processes even with 0% CPU initially, sort might bring up mem hogs
            valid_processes = [p for p in all_procs_info if p['pid'] != 0] # Exclude PID 0 (Idle/System)

            # Sort primarily by CPU, secondarily by Memory
            sorted_processes = sorted(valid_processes, key=lambda x: (x['cpu_percent'], x['memory_percent']), reverse=True)[:limit]

            # Rename keys to match UI expectations
            for proc in sorted_processes:
                proc['PID'] = proc.pop('pid')
                proc['Name'] = proc.pop('name')
                proc['CPU%'] = proc['cpu_percent']
                proc['Memory%'] = proc['memory_percent']

            return sorted_processes

        except Exception as e:
            logging.error(f"Error in _get_process_info: {str(e)}", exc_info=True)
            return [] # Return empty list on major error

    def collect(self) -> Dict[str, Any]:
        logging.debug("Starting metrics collection")
        try:
            # Get CPU usage first - interval is important here
            cpu_data = self._get_cpu_info()

            # Collect other metrics
            current_metrics = {
                'timestamp': datetime.now(),
                'cpu': cpu_data, # Use the already collected CPU data
                'memory': self._get_memory_info(),
                'disk': self._get_disk_info(),
                'network': self._get_network_info(), # Relies on time delta
                'processes': self._get_process_info(),
                'gpu': self._get_gpu_info() # Collect GPU info
            }
            logging.debug(f"Collected metrics.") # Don't log the full dict in debug usually

            # Store in history
            for metric, value in current_metrics.items():
                if metric != 'timestamp':
                     # Ensure the key exists before appending
                    if metric not in self.history:
                        self.history[metric] = deque(maxlen=self.history_length)
                    self.history[metric].append({
                        'timestamp': current_metrics['timestamp'],
                        'data': value
                    })

            return current_metrics
        except Exception as e:
            logging.error(f"Error in collect: {str(e)}", exc_info=True)
            # Return an empty structure or re-raise depending on desired handling
            return {} # Return empty dict to avoid crashing the loop


class AlertManager:
    def __init__(self):
        self.thresholds = {
            'cpu_average': 85.0, # Alert if average CPU > 85%
            'cpu_core_spike': 95.0, # Alert if any single core > 95%
            'cpu_cores_high': 3, # Alert if this many cores are > 80%
            'memory_percent': 90.0,
            'disk_percent': 90.0,
            'swap_percent': 70.0,
            'gpu_memory': 85.0,
            'gpu_temperature': 85.0,
            'gpu_load': 90.0  # Alert if GPU load > 90%
        }
        self.alerts = deque(maxlen=20)

    def check_metrics(self, metrics: Dict) -> List[Dict]:
        logging.debug(f"Checking metrics for alerts")
        new_alerts = []
        now = datetime.now()

        if not metrics: # Handle empty metrics dict from collector error
             return []

        try:
            # CPU alerts - check average AND per-core spikes
            if cpu_data := metrics.get('cpu'):
                # Average CPU check
                if 'Average Usage' in cpu_data:
                    cpu_usage = cpu_data['Average Usage']
                    if cpu_usage > self.thresholds['cpu_average']:
                        new_alerts.append({
                            'timestamp': now,
                            'level': 'critical' if cpu_usage > 95 else 'warning',
                            'component': 'CPU',
                            'message': f"High CPU usage: {cpu_usage:.1f}%"
                        })

                # Per-core spike check
                if 'Per Core Usage' in cpu_data:
                    per_core = cpu_data['Per Core Usage']
                    max_core = max(per_core) if per_core else 0
                    cores_above_80 = sum(1 for c in per_core if c > 80)

                    # Alert on single core spike
                    if max_core > self.thresholds['cpu_core_spike']:
                        core_idx = per_core.index(max_core)
                        new_alerts.append({
                            'timestamp': now,
                            'level': 'warning',
                            'component': 'CPU',
                            'message': f"Core {core_idx} spike: {max_core:.0f}%"
                        })
                    # Alert on multiple high cores
                    elif cores_above_80 >= self.thresholds['cpu_cores_high']:
                        new_alerts.append({
                            'timestamp': now,
                            'level': 'warning',
                            'component': 'CPU',
                            'message': f"{cores_above_80} cores above 80%"
                        })

            # Memory alerts
            if mem_data := metrics.get('memory'):
                if 'Percentage' in mem_data:
                    mem_usage = mem_data['Percentage']
                    if mem_usage > self.thresholds['memory_percent']:
                        new_alerts.append({
                             'timestamp': now,
                            'level': 'critical' if mem_usage > 95 else 'warning',
                            'component': 'Memory',
                            'message': f"High memory usage: {mem_usage}%"
                        })
                # Swap alert
                if 'Swap Percentage' in mem_data:
                     swap_usage = mem_data['Swap Percentage']
                     if swap_usage > self.thresholds['swap_percent']:
                          new_alerts.append({
                              'timestamp': now,
                              'level': 'warning',
                              'component': 'Swap',
                              'message': f"High swap usage: {swap_usage}%"
                          })

            # Disk alerts
            if disk_data := metrics.get('disk'):
                for disk in disk_data:
                    if disk.get('Percentage', 0) > self.thresholds['disk_percent']:
                        new_alerts.append({
                             'timestamp': now,
                            'level': 'warning',
                            'component': 'Disk',
                            'message': f"High disk usage on {disk.get('Mountpoint', 'Unknown')}: {disk['Percentage']}%"
                        })

            # GPU alerts
            if gpu_data := metrics.get('gpu'):
                for gpu in gpu_data:
                    gpu_name = gpu.get('Name', 'Unknown GPU')
                    # Check GPU memory utilization - Safely convert to float
                    try:
                        mem_util_str = gpu.get('Memory Util', 'N/A').replace('%', '')
                        if mem_util_str != 'N/A':
                            mem_util = float(mem_util_str)
                            if mem_util > self.thresholds['gpu_memory']:
                                new_alerts.append({
                                     'timestamp': now,
                                    'level': 'critical' if mem_util > 95 else 'warning',
                                    'component': 'GPU',
                                    'message': f"High GPU memory on {gpu_name}: {mem_util:.1f}%"
                                })
                    except (ValueError, TypeError):
                        logging.debug(f"Could not parse GPU Memory Util for {gpu_name}: {gpu.get('Memory Util')}")
                        pass # Ignore if not a valid number or N/A

                    # Check GPU temperature - Safely convert to float
                    try:
                        temp_str = gpu.get('Temperature', 'N/A').replace('°C', '')
                        if temp_str != 'N/A':
                            temp = float(temp_str)
                            if temp > self.thresholds['gpu_temperature']:
                                new_alerts.append({
                                     'timestamp': now,
                                    'level': 'critical' if temp > 90 else 'warning',
                                    'component': 'GPU',
                                    'message': f"High GPU temperature on {gpu_name}: {temp}°C"
                                })
                    except (ValueError, TypeError):
                        logging.debug(f"Could not parse GPU Temperature for {gpu_name}: {gpu.get('Temperature')}")
                        pass # Ignore if not a valid number or N/A

                    # Check GPU load (for Apple Silicon via ioreg)
                    try:
                        load_str = gpu.get('Load', 'N/A').replace('%', '')
                        if load_str != 'N/A':
                            load = float(load_str)
                            if load > self.thresholds['gpu_load']:
                                new_alerts.append({
                                    'timestamp': now,
                                    'level': 'critical' if load > 95 else 'warning',
                                    'component': 'GPU',
                                    'message': f"High GPU load: {load:.0f}%"
                                })
                    except (ValueError, TypeError):
                        pass

            # Append new alerts only if they are different from the last one for the same component
            if new_alerts:
                 last_alerts_by_component = {a['component']: a['message'] for a in self.alerts}
                 for alert in new_alerts:
                      if alert['component'] not in last_alerts_by_component or \
                         last_alerts_by_component[alert['component']] != alert['message']:
                           self.alerts.append(alert)

            # Return only the *newly triggered* alerts in this cycle for potential actions
            return new_alerts

        except Exception as e:
            logging.error(f"Error in check_metrics: {str(e)}", exc_info=True)
            return []


class SystemAnalyzer:
    def __init__(self, use_openai: bool = False):
        self.use_openai = use_openai
        self.client = None
        self.ollama_available = False # Track Ollama availability
        self.current_model = "llama3:8b" # Default Ollama model
        self.available_models = [] # Cache of available models

        if use_openai:
            try:
                import openai
                # Ensure API key is set
                if not os.getenv("OPENAI_API_KEY"):
                    logging.warning("OPENAI_API_KEY environment variable not set. OpenAI analysis disabled.")
                    self.use_openai = False
                else:
                    self.client = openai.OpenAI() # Simpler init if key is env var
                    logging.info("OpenAI client initialized.")
            except ImportError:
                logging.warning("OpenAI package not installed (pip install openai). OpenAI analysis disabled.")
                self.use_openai = False
            except Exception as e:
                 logging.error(f"Failed to initialize OpenAI client: {e}")
                 self.use_openai = False
        else:
             # Try Ollama if not using OpenAI
            try:
                import ollama
                # Check if Ollama server is running by listing models
                models_response = ollama.list()
                self.ollama_available = True
                self._update_available_models(models_response)
                logging.info("Ollama client appears available.")
            except ImportError:
                 logging.info("Ollama package not installed (pip install ollama). Using local analysis.")
            except Exception as e:
                 logging.warning(f"Ollama connection error ({e}). Is the Ollama server running? Using local analysis.")


        self.analyses = deque(maxlen=20) # Keep more analyses for feed
        # Removed visible_offset as we'll display based on available space

    def _update_available_models(self, models_response):
        """Update the list of available models from Ollama response."""
        try:
            self.available_models = [m.get("name", "") for m in models_response.get("models", [])]
            logging.debug(f"Available Ollama models: {self.available_models}")
            # Set current_model to first available if current one is not available
            if self.current_model not in self.available_models and self.available_models:
                self.current_model = self.available_models[0]
        except Exception as e:
            logging.error(f"Error parsing available models: {e}")
            self.available_models = []

    def refresh_available_models(self):
        """Fetch fresh list of available models from Ollama server."""
        if not self.ollama_available:
            return False
        try:
            import ollama
            models_response = ollama.list()
            self._update_available_models(models_response)
            return True
        except Exception as e:
            logging.warning(f"Failed to refresh models: {e}")
            return False

    def set_model(self, model_name: str) -> bool:
        """Set the current Ollama model. Returns True if successful."""
        if model_name in self.available_models:
            self.current_model = model_name
            logging.info(f"Switched to model: {model_name}")
            return True
        else:
            logging.warning(f"Model {model_name} not in available models")
            return False


    def get_visible_analyses(self, max_lines: int) -> List[Dict]:
        """Get the most recent analyses that fit within max_lines."""
        visible = []
        current_lines = 0
        for analysis_item in reversed(self.analyses):
            # Estimate lines: timestamp + message lines + blank line separator
            # Rough estimate: 1 line for timestamp + message / 60 chars + 1 blank line
            est_lines = 1 + (len(analysis_item['analysis']) // 60 + 1) + 1
            if current_lines + est_lines > max_lines:
                break
            visible.insert(0, analysis_item) # Insert at beginning to keep chronological order
            current_lines += est_lines
        return visible


    def get_previous_analyses(self, count: int = 2) -> List[str]:
        """Get the last N analyses text"""
        return [a['analysis'] for a in list(self.analyses)[-count:]]


    def _prepare_metrics_summary(self, metrics: Dict) -> str:
        """Create a detailed summary string for the LLM to analyze."""
        try:
            summary = []

            # CPU with per-core context
            if cpu := metrics.get('cpu'):
                avg_usage = cpu.get('Average Usage', 0)
                per_core = cpu.get('Per Core Usage', [])
                max_core = max(per_core) if per_core else 0
                cores_above_50 = sum(1 for c in per_core if c > 50)
                summary.append(f"CPU avg={avg_usage:.0f}%, max_core={max_core:.0f}%, {cores_above_50} cores>50%")

            # Memory with swap context
            if mem := metrics.get('memory'):
                usage = mem.get('Percentage', 0)
                swap = mem.get('Swap Percentage', 0)
                available = mem.get('Available', 'N/A')
                summary.append(f"Memory={usage:.0f}%, swap={swap:.0f}%, available={available}")

            # GPU
            if gpus := metrics.get('gpu'):
                for gpu in gpus:
                    name = gpu.get('Name', 'GPU')
                    load_str = gpu.get('Load', 'N/A')
                    mem_used = gpu.get('Memory Used', 'N/A')
                    summary.append(f"GPU {name}: load={load_str}, mem={mem_used}")

            # Network rates
            if net := metrics.get('network'):
                recv = net.get('Recv Rate', '0 bps')
                sent = net.get('Sent Rate', '0 bps')
                summary.append(f"Network: in={recv}, out={sent}")

            # Top processes with both CPU and Memory
            if procs := metrics.get('processes'):
                proc_info = []
                for p in procs[:5]:
                    cpu_pct = p.get('CPU%', 0)
                    mem_pct = p.get('Memory%', 0)
                    name = p.get('Name', '?')[:12]
                    proc_info.append(f"{name}(cpu={cpu_pct:.0f}%,mem={mem_pct:.1f}%)")
                summary.append(f"Processes: {', '.join(proc_info)}")

            return " | ".join(summary)

        except Exception as e:
            logging.error(f"Error preparing metrics summary: {str(e)}")
            return "Error preparing metrics data"

    def analyze(self, metrics: Dict, history: Dict) -> str:
        analysis = "Analysis unavailable." # Default
        try:
            metrics_summary = self._prepare_metrics_summary(metrics)

            # Prompt for insightful analysis
            prompt_context = f"""System metrics: {metrics_summary}

You are a system analyst. Provide ONE brief insight (max 15 words) about what's happening:
- Identify the ROOT CAUSE if resources are high (which process? why?)
- Note interesting patterns (high single core = single-threaded task, swap active = memory pressure)
- If system is idle, say "System idle" and nothing more
- Be specific: name processes and explain their impact
- Never just repeat numbers - explain what they MEAN

Examples of GOOD insights:
- "Safari tabs causing memory pressure, swap active"
- "Claude doing heavy computation on 2 cores"
- "Ollama inference saturating GPU"
- "System idle, all resources normal"

Examples of BAD insights (don't do these):
- "CPU high at 92%" (just repeating data)
- "Some issues detected" (vague)
- "High utilization" (meaningless)

Insight:"""

            if self.use_openai and self.client:
                try:
                    response = self.client.chat.completions.create(
                        model="gpt-4o-mini", # Cheaper and faster model
                        messages=[
                            {"role": "system", "content": "You are a concise system monitor assistant."},
                            {"role": "user", "content": prompt_context}
                        ],
                        max_tokens=30,
                        temperature=0.2,
                        n=1,
                        stop=None,
                    )
                    analysis = response.choices[0].message.content.strip()
                    logging.info(f"OpenAI analysis: {analysis}")
                except Exception as e:
                     logging.error(f"OpenAI API call failed: {e}")
                     analysis = self._generate_local_analysis(metrics) # Fallback

            elif self.ollama_available:
                try:
                    import ollama
                    response = ollama.chat(
                        model=self.current_model,
                        messages=[{
                            "role": "user",
                            "content": prompt_context
                        }],
                        options={"temperature": 0.3, "num_predict": 40} # Longer, focused insights
                    )
                    analysis = response['message']['content'].strip()
                    # Clean up: take only first line, remove quotes
                    analysis = analysis.split('\n')[0].strip('"').strip()
                    logging.info(f"Ollama analysis ({self.current_model}): {analysis}")
                except Exception as e:
                    logging.error(f"Ollama API call failed ({self.current_model}): {e}")
                    analysis = self._generate_local_analysis(metrics) # Fallback
            else:
                # Fallback to simple rule-based analysis
                analysis = self._generate_local_analysis(metrics)
                logging.info(f"Local analysis: {analysis}")


            # Clean up the analysis text
            analysis = analysis.replace('\n', ' ').strip()
            if not analysis: # Handle empty LLM response
                 analysis = self._generate_local_analysis(metrics)

            # Always append analysis to create a feed (with timestamp)
            self.analyses.append({
                'timestamp': datetime.now(),
                'analysis': analysis,
            })

            return analysis

        except Exception as e:
            error_msg = f"Analysis Error: {str(e)}"
            logging.error(error_msg, exc_info=True)
            # Don't append error messages to the analysis history repeatedly
            return "Analysis temporarily unavailable."


    def _generate_local_analysis(self, metrics: Dict) -> str:
        """Generate a simple rule-based analysis without using an LLM"""
        try:
            cpu_usage = metrics.get('cpu', {}).get('Average Usage', 0)
            memory_usage = metrics.get('memory', {}).get('Percentage', 0)
            processes = metrics.get('processes', [])
            gpus = metrics.get('gpu', [])

            # Check GPU first
            for gpu in gpus:
                 gpu_name = gpu.get('Name', 'GPU')[:15]
                 try:
                    temp_str = gpu.get('Temperature', 'N/A').replace('°C', '')
                    if temp_str != 'N/A' and float(temp_str) > 85:
                         return f"High temp on {gpu_name}: {temp_str}°C!"
                 except (ValueError, TypeError): pass
                 try:
                    mem_util_str = gpu.get('Memory Util', 'N/A').replace('%', '')
                    if mem_util_str != 'N/A' and float(mem_util_str) > 85:
                         return f"High VRAM on {gpu_name}: {mem_util_str}%!"
                 except (ValueError, TypeError): pass
                 try:
                    load_str = gpu.get('Load', 'N/A').replace('%', '')
                    if load_str != 'N/A' and float(load_str) > 85:
                        return f"High load on {gpu_name}: {load_str}%!"
                 except (ValueError, TypeError): pass

            if cpu_usage > 85:
                top_proc = f" by {processes[0]['Name']}" if processes else ""
                return f"High CPU load: {cpu_usage:.1f}%{top_proc}"
            elif memory_usage > 85:
                 top_proc = f" by {processes[0]['Name']}" if processes else ""
                 return f"High Memory usage: {memory_usage:.1f}%{top_proc}"

            # Check for high single process CPU
            if processes and processes[0].get('CPU%', 0) > 50:
                 return f"{processes[0]['Name']} using {processes[0]['CPU%']:.0f}% CPU."

            # Normal state - show current stats summary
            top_proc_name = processes[0]['Name'] if processes else "idle"
            gpu_info = ""
            if gpus:
                gpu_load = gpus[0].get('Load', 'N/A')
                if gpu_load != 'N/A':
                    gpu_info = f", GPU {gpu_load}"
            return f"Stable: CPU {cpu_usage:.0f}%, Mem {memory_usage:.0f}%{gpu_info}, top: {top_proc_name}"

        except Exception as e:
            logging.error(f"Error in local analysis: {str(e)}")
            return "Unable to generate local analysis."


class UIComponent:
    # Create table method adjusted to handle potentially missing keys gracefully
    def _create_table_from_dict(self, data: Dict, title: str, color: str = "white") -> Table:
        table = Table(show_header=False, box=box.SIMPLE, padding=(0, 0))
        table.add_column("Key", style=f"bold {color}", no_wrap=True)
        table.add_column("Value", style="white", no_wrap=True)
        for key, value in data.items():
            table.add_row(str(key), str(value))
        return table

    def _create_table_from_list(self, data: List[Dict], title: str, color: str = "white") -> Table:
        table = Table(box=box.SIMPLE, padding=(0, 0), show_header=True)

        if not data:
            table.show_header = False
            table.add_column("Status", style="dim white")
            table.add_row(f"No {title.lower()} data")
            return table

        headers = list(data[0].keys())
        # Define column styles and justifications (customize as needed)
        col_styles = {"default": "white"}
        col_justify = {"default": "left"}
        col_width = {}

        # --- Special handling for known table types ---
        # Color scheme: cyan (stats), yellow (warnings/storage), green (status)
        if title.lower() == "top processes":
            headers = ["PID", "Name", "CPU%", "Memory%"] # Enforce order/presence
            col_styles.update({"PID": "dim", "CPU%": "green", "Memory%": "cyan"})
            col_justify.update({"PID": "right", "CPU%": "right", "Memory%": "right"})
            col_width = {"PID": 7, "Name": None, "CPU%": 6, "Memory%": 8} # Let Name flex

        elif title.lower() == "gpu":
            # Dynamically select columns based on GPU type and available data
            gpu_type = data[0].get("Type", "Unknown") if data else "Unknown"
            has_load = any(gpu.get("Load") != "N/A" for gpu in data)
            has_mem_util = any(gpu.get("Memory Util") != "N/A" for gpu in data)
            has_mem_used = any(gpu.get("Memory Used") != "N/A" for gpu in data)
            has_temp = any(gpu.get("Temperature") != "N/A" for gpu in data)

            if has_mem_util:
                # NVIDIA with full metrics
                headers = ["Name", "Load", "Memory Util", "Temperature"]
                col_styles.update({"Load": "green", "Memory Util": "cyan", "Temperature": "yellow"})
                col_justify.update({"Load": "right", "Memory Util": "right", "Temperature": "right"})
                col_width = {"Name": None, "Load": 8, "Memory Util": 12, "Temperature": 8}
            elif has_load and has_mem_used:
                # Apple Silicon with ioreg (Load + Memory Used available)
                headers = ["Name", "Load", "Memory Used"]
                col_styles.update({"Load": "green", "Memory Used": "cyan"})
                col_justify.update({"Load": "right", "Memory Used": "right"})
                col_width = {"Name": None, "Load": 6, "Memory Used": 10}
            elif has_load:
                # Apple Silicon with Load only
                headers = ["Name", "Load", "Memory Total"]
                col_styles.update({"Load": "green", "Memory Total": "cyan"})
                col_justify.update({"Load": "right", "Memory Total": "right"})
                col_width = {"Name": None, "Load": 6, "Memory Total": 10}
            else:
                # No real-time data - show static info only
                headers = ["Name", "Memory Total", "Type"]
                col_styles.update({"Memory Total": "cyan", "Type": "dim"})
                col_justify.update({"Memory Total": "right"})
                col_width = {"Name": None, "Memory Total": 10, "Type": 8}

        elif title.lower() == "disk":
             headers = ["Mountpoint", "Used", "Total", "Percentage"]
             col_styles.update({"Percentage": "yellow", "Used": "cyan", "Total": "dim"})
             col_justify.update({"Used": "right", "Total": "right", "Percentage": "right"})
             col_width = {"Mountpoint": None, "Used": 8, "Total": 8, "Percentage": 6}


        # Add columns with specific widths if defined
        for header in headers:
             table.add_column(
                  header,
                  style=col_styles.get(header, col_styles["default"]),
                  justify=col_justify.get(header, col_justify["default"]),
                  width=col_width.get(header) # Will be None if not specified, allowing Rich to auto-size
             )

        # Add rows, ensuring all headers exist in the data dict
        for item in data:
            row_values = []
            for header in headers:
                 value = item.get(header, "N/A") # Default to N/A if key missing
                 # Formatting specific types
                 if header == "CPU%" or header == "Memory%":
                      try:
                          row_values.append(f"{float(value):.1f}")
                      except (ValueError, TypeError):
                           row_values.append(str(value)) # Keep original if not float
                 elif header == "Name":
                      row_values.append(str(value)) # No truncation here, let Rich handle overflow
                 else:
                    row_values.append(str(value))
            table.add_row(*row_values)

        return table


    def create_displayable(self, data: Any, title: str, color: str = "white") -> Table:
        """Dispatcher to create the correct table type."""
        if isinstance(data, dict):
            return self._create_table_from_dict(data, title, color)
        elif isinstance(data, list):
            return self._create_table_from_list(data, title, color)
        else:
            # Fallback for unexpected data types
            table = Table(show_header=False, box=box.ROUNDED)
            table.add_column("Info")
            table.add_row(f"Unsupported data type for {title}: {type(data)}")
            return table


class SystemMonitor:
    def __init__(self, update_frequency: int = 5, use_openai: bool = False, history_length: int = 60):
        self.update_frequency = max(1, update_frequency) # Ensure frequency is at least 1
        # Initialize collector with history length
        self.metrics_collector = SystemMetricsCollector(history_length=history_length)
        self.alert_manager = AlertManager()
        self.analyzer = SystemAnalyzer(use_openai=use_openai)
        self.ui = UIComponent()
        self.current_layout: Optional[Layout] = None # Cache layout
        self._last_has_gpu: bool = False # Track GPU state for layout changes
        self._old_terminal_settings = None  # For keyboard input

    def _check_keypress(self) -> Optional[str]:
        """Check for keypress without blocking. Returns the key or None."""
        try:
            # Check if input is available
            if select.select([sys.stdin], [], [], 0)[0]:
                return sys.stdin.read(1)
        except Exception:
            pass
        return None

    def _setup_keyboard(self):
        """Set terminal to raw mode for single keypress detection."""
        try:
            self._old_terminal_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        except Exception:
            self._old_terminal_settings = None

    def _restore_keyboard(self):
        """Restore terminal to normal mode."""
        if self._old_terminal_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_terminal_settings)
            except Exception:
                pass

    def show_model_selector_menu(self):
        """Display an interactive model selector menu and let user choose."""
        # Refresh available models
        self.analyzer.refresh_available_models()
        available = self.analyzer.available_models

        if not available:
            console.print("[yellow]No Ollama models available[/yellow]")
            return

        console.print("\n[cyan bold]Select Ollama Model[/cyan bold]")
        console.print("-" * 40)

        for i, model in enumerate(available, 1):
            is_current = " [CURRENT]" if model == self.analyzer.current_model else ""
            console.print(f"  {i}. {model}{is_current}")

        console.print("-" * 40)
        console.print("Enter model number (1-{}) or 'q' to cancel: ".format(len(available)), end="", highlight=False)
        sys.stdout.flush()

        try:
            user_input = input().strip().lower()

            if user_input == 'q':
                console.print("[dim]Cancelled[/dim]")
                return

            choice = int(user_input) - 1
            if 0 <= choice < len(available):
                selected = available[choice]
                self.analyzer.set_model(selected)
                console.print(f"[green]✓ Switched to {selected}[/green]")
            else:
                console.print("[red]Invalid selection[/red]")
        except (ValueError, EOFError):
            console.print("[red]Invalid input[/red]")

        time.sleep(1)  # Brief pause before resuming

    def generate_layout(self, current_metrics: Dict, alerts: List[Dict], analysis: str) -> Layout:
        # Check if GPU data exists (show panel even if only static info available)
        gpu_data = current_metrics.get('gpu', [])
        has_gpu = len(gpu_data) > 0  # Show GPU panel if ANY GPU detected

        # Create layout structure only once if it doesn't exist OR if GPU status changed
        if not self.current_layout or self._last_has_gpu != has_gpu:
            layout = Layout()
            layout.split_column(
                # Header: Compact system info (2 lines)
                Layout(name="header", size=2),
                Layout(name="body"),
                # Footer: Status/Timestamp
                Layout(name="footer", size=1)
            )

            layout["body"].split_row(
                Layout(name="main", ratio=5),
                Layout(name="side", ratio=2)
            )

            layout["main"].split_column(
                Layout(name="top_row", ratio=1), # CPU, Mem
                Layout(name="middle_row", ratio=1), # Net, Disk
                Layout(name="bottom_row", ratio=2) # Processes (2/3) + Alerts (1/3)
            )
            layout["main"]["top_row"].split_row(Layout(name="cpu"), Layout(name="memory"))
            layout["main"]["middle_row"].split_row(Layout(name="network"), Layout(name="disk"))

            # Bottom row: Processes (3/4) and Alerts (1/4) side by side
            layout["main"]["bottom_row"].split_row(
                Layout(name="processes", ratio=3),
                Layout(name="alerts", ratio=1)
            )

            # Side: conditionally add GPU based on whether data exists
            if has_gpu:
                layout["side"].split_column(
                    Layout(name="gpu", size=8), # GPU Info - enough for header + 1-2 rows
                    Layout(name="analysis") # Analysis feed takes remaining space
                )
            else:
                # No GPU: Analysis takes full height
                layout["side"].split_column(
                    Layout(name="analysis")
                )

            self.current_layout = layout
            self._last_has_gpu = has_gpu
        else:
             layout = self.current_layout

        # --- Update Panels with current data ---
        metrics = current_metrics # Alias for brevity

        # Header: Compact system info
        sys_info = self.metrics_collector.system_info
        uptime = self.metrics_collector._get_uptime_string()
        header_text = Text()
        header_text.append(f" {sys_info.get('Node Name', 'unknown')}", style="bold cyan")
        header_text.append(f"  {sys_info.get('System', '')} {sys_info.get('Release', '')}", style="dim")
        header_text.append(f"  {sys_info.get('Machine', '')}", style="dim")
        header_text.append(f"  up {uptime}", style="green")
        layout["header"].update(Panel(header_text, box=box.SIMPLE, style="dim"))

        # CPU (cyan - system stats)
        layout["main"]["top_row"]["cpu"].update(
            Panel(self.ui.create_displayable(metrics.get('cpu',{}), "CPU", "cyan"),
                  title="CPU", box=box.ROUNDED, border_style="cyan")
        )
        # Memory (cyan - system stats)
        layout["main"]["top_row"]["memory"].update(
             Panel(self.ui.create_displayable(metrics.get('memory',{}), "Memory", "cyan"),
                  title="Memory", box=box.ROUNDED, border_style="cyan")
        )
        # Network (cyan - system stats)
        layout["main"]["middle_row"]["network"].update(
             Panel(self.ui.create_displayable(metrics.get('network',{}), "Network", "cyan"),
                   title="Network", box=box.ROUNDED, border_style="cyan")
        )
        # Disk (yellow - storage/warnings)
        layout["main"]["middle_row"]["disk"].update(
             Panel(self.ui.create_displayable(metrics.get('disk',[]), "Disk", "yellow"),
                  title="Disk", box=box.ROUNDED, border_style="yellow")
        )
        # Processes (green - status)
        layout["main"]["bottom_row"]["processes"].update(
             Panel(self.ui.create_displayable(metrics.get('processes',[]), "Top Processes", "green"),
                   title="Processes", box=box.ROUNDED, border_style="green")
        )
        # GPU (green - status)
        if has_gpu:
            layout["side"]["gpu"].update(
                Panel(self.ui.create_displayable(metrics.get('gpu',[]), "GPU", "green"),
                      title="GPU", box=box.ROUNDED, border_style="green")
            )

        # Analysis Panel - show feed of recent analyses
        # Show up to last 10 analyses (deque size), newest at bottom
        analysis_renderables = []
        for item in list(self.analyzer.analyses):
            ts = Text(f"[{item['timestamp'].strftime('%H:%M:%S')}] ", style="dim cyan")
            # Truncate long messages to fit panel width
            msg_text = item['analysis'][:80] + "..." if len(item['analysis']) > 80 else item['analysis']
            msg = Text(msg_text, style="white")
            analysis_renderables.append(Text.assemble(ts, msg))

        layout["side"]["analysis"].update(
            Panel(Group(*analysis_renderables) if analysis_renderables else Text("Waiting for analysis...", style="dim"),
                  title="Analysis", box=box.ROUNDED, border_style="green")
        )

        # Alerts Panel
        alert_renderables = []
        # Show most recent alerts first
        for alert in reversed(list(self.alert_manager.alerts)):
             color = "yellow" if alert['level'] == 'warning' else "red"
             ts = Text(f"[{alert['timestamp'].strftime('%H:%M:%S')}]", style=f"dim {color}")
             msg = Text(f" {alert['component']}: {alert['message']}", style=color)
             alert_renderables.append(Text.assemble(ts, msg))

        layout["main"]["bottom_row"]["alerts"].update(
            Panel(Group(*alert_renderables) if alert_renderables else Text("No active alerts", style="dim green"),
                  title="Alerts", box=box.ROUNDED, border_style="yellow")
        )

        # Footer with keyboard shortcuts hint
        model_info = f" | Model: {self.analyzer.current_model}" if self.analyzer.ollama_available else ""
        shortcuts = " | [M]odel [Q]uit" if self.analyzer.ollama_available else " | [Q]uit"
        layout["footer"].update(
             Text(f"Updated: {datetime.now():%H:%M:%S} | Refresh: {self.update_frequency}s{model_info}{shortcuts}",
                   style="dim", justify="center")
        )

        return layout

    def run(self):
        analysis_text = "Initializing..."
        logging.info(f"Starting system monitor (update: {self.update_frequency}s)")

        # Perform an initial collection to populate data structures before first render
        initial_metrics = self.metrics_collector.collect()
        initial_alerts = self.alert_manager.check_metrics(initial_metrics)
        # Don't run analysis on the very first data point to avoid weird initial state
        initial_analysis = "Waiting for data..."

        # Setup keyboard for hotkeys
        self._setup_keyboard()

        try:
            with Live(
                # Render initial layout immediately
                self.generate_layout(initial_metrics, initial_alerts, initial_analysis),
                refresh_per_second=max(1, 4 // self.update_frequency), # Refresh UI faster than data collection
                screen=True,
                transient=False # Keep last frame on exit
            ) as live:
                # Main loop state
                last_analysis_time = time.monotonic()
                analysis_interval = max(5, self.update_frequency * 2) # Analyze every ~6-10 seconds

                while True:
                    try:
                        collect_start = time.monotonic()

                        # Check for keyboard shortcuts
                        key = self._check_keypress()
                        if key:
                            if key.lower() == 'm' and self.analyzer.ollama_available:
                                # Exit live display, show model selector, restart
                                live.stop()
                                self._restore_keyboard()
                                self.show_model_selector_menu()
                                self._setup_keyboard()
                                live.start()
                            elif key.lower() == 'q':
                                # Quit
                                break

                        # Collect metrics
                        current_metrics = self.metrics_collector.collect()
                        if not current_metrics: # Handle collector failure
                             logging.warning("Metric collection failed, skipping update cycle.")
                             time.sleep(self.update_frequency)
                             continue

                        # Check for alerts
                        alerts = self.alert_manager.check_metrics(current_metrics)

                        # Generate analysis periodically
                        current_time = time.monotonic()
                        if current_time - last_analysis_time >= analysis_interval:
                             logging.debug("Generating analysis...")
                             analysis_text = self.analyzer.analyze(
                                 current_metrics,
                                 self.metrics_collector.history
                             )
                             last_analysis_time = current_time
                             logging.debug("Analysis finished.")
                        # Use existing analysis_text if not time to regenerate

                        # Update the live display
                        live.update(
                            self.generate_layout(
                                current_metrics,
                                alerts, # Pass current alerts list (AlertManager stores history)
                                analysis_text # Pass current analysis text
                            )
                        )

                        # Wait efficiently for next update
                        collect_duration = time.monotonic() - collect_start
                        wait_time = self.update_frequency - collect_duration
                        if wait_time > 0:
                             time.sleep(wait_time)

                    except KeyboardInterrupt:
                        logging.info("Keyboard interrupt received. Exiting.")
                        console.print("[bold yellow]Monitoring stopped by user.[/bold yellow]")
                        break
                    except Exception as e:
                        # Log error, but try to continue the loop
                        logging.error(f"Error in main loop: {str(e)}", exc_info=True)
                        # Display error briefly in UI? Maybe just log it.
                        console.print(f"[bold red]Runtime Error: {str(e)} - Check system_monitor.log[/bold red]")
                        # Prevent rapid error loops
                        time.sleep(max(self.update_frequency, 2))

        except Exception as e:
            logging.critical(f"Fatal error initializing or running Live display: {str(e)}", exc_info=True)
            console.print_exception(show_locals=False) # Print traceback to console
            print(f"\nFatal error: {e}. Check system_monitor.log for details.")
        finally:
            # Always restore terminal settings
            self._restore_keyboard()


def main():
    parser = argparse.ArgumentParser(description="llmtop - Enhanced System Monitor")
    parser.add_argument(
        "-f", "--update-frequency",
        type=int,
        default=3, # Default to faster updates
        help="Update frequency in seconds (default: 3)"
    )
    parser.add_argument(
        "--use-openai",
        action="store_true",
        help="Use OpenAI for analysis (requires OPENAI_API_KEY env var)"
    )
    parser.add_argument(
        "--history", "--history-length",
        type=int,
        default=60,
        dest="history_length", # Ensure destination matches constructor arg
        help="Number of historical data points to keep per metric (default: 60)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (writes more to system_monitor.log)"
    )
    parser.add_argument(
        "-m", "--select-model",
        action="store_true",
        help="Show model selection menu before starting monitoring"
    )
    args = parser.parse_args()

    if args.debug:
         logging.getLogger().setLevel(logging.DEBUG)
         logging.debug("Debug logging enabled.")



    # Initialize the monitor
    monitor = SystemMonitor(
        update_frequency=args.update_frequency,
        use_openai=args.use_openai,
        history_length=args.history_length # Pass history length
    )

    # Show model selection menu if requested
    if args.select_model and monitor.analyzer.ollama_available:
        monitor.show_model_selector_menu()

    # Run the monitor
    monitor.run()


if __name__ == "__main__":
    main()