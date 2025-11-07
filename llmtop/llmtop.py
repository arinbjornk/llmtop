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

# Try importing GPUtil, but don't fail if it's not there
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
        """Attempt to get Apple Silicon GPU info without sudo."""
        gpu_info = []
        try:
            # Use system_profiler to get basic GPU info
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True, check=True)
            output = result.stdout

            # Basic parsing (this might need adjustment based on exact macOS version/output)
            gpu_name = "Apple Silicon GPU"
            vram = "N/A"
            in_graphics_section = False
            for line in output.splitlines():
                line = line.strip()
                if "Graphics/Displays:" in line:
                    in_graphics_section = True
                    continue
                if in_graphics_section:
                    if line.startswith("Chipset Model:"):
                        gpu_name = line.split(":", 1)[1].strip()
                    elif line.startswith("VRAM (Total):") or line.startswith("VRAM (Dynamic, Max):"):
                         vram_str = line.split(":", 1)[1].strip()
                         # Keep original VRAM string (e.g., "8 GB")
                         vram = vram_str
                    elif line == "" and gpu_name != "Apple Silicon GPU": # End of a GPU section
                         break # Assuming one main integrated GPU for now

            # NOTE: Real-time Load, Memory Usage %, and Temperature are typically
            # not available without elevated privileges (sudo) using tools like 'powermetrics'.
            # We provide placeholders here.
            gpu_info.append({
                "ID": 0,
                "Name": gpu_name,
                "Load": "N/A", # Requires specific tools/privileges
                "Memory Used": "N/A", # Requires specific tools/privileges
                "Memory Total": vram, # Static total VRAM
                "Memory Util": "N/A", # Requires specific tools/privileges
                "Temperature": "N/A", # Requires specific tools/privileges
                "UUID": "apple-silicon",
                "Type": "Apple"
            })
        except (FileNotFoundError, subprocess.CalledProcessError, Exception) as e:
            logging.warning(f"Could not get Apple Silicon GPU info via system_profiler: {e}")
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
            'cpu_average': 85.0, # Adjusted threshold
            'memory_percent': 90.0,
            'disk_percent': 90.0, # Adjusted threshold
            'swap_percent': 70.0, # Adjusted threshold
            'gpu_memory': 85.0,
            'gpu_temperature': 85.0 # Adjusted threshold
        }
        self.alerts = deque(maxlen=20) # Keep fewer, more relevant alerts

    def check_metrics(self, metrics: Dict) -> List[Dict]:
        logging.debug(f"Checking metrics for alerts")
        new_alerts = []
        now = datetime.now()

        if not metrics: # Handle empty metrics dict from collector error
             return []

        try:
            # CPU alerts
            if cpu_data := metrics.get('cpu'): # Use walrus operator for cleaner check
                if 'Average Usage' in cpu_data:
                    cpu_usage = cpu_data['Average Usage']
                    if cpu_usage > self.thresholds['cpu_average']:
                        new_alerts.append({
                            'timestamp': now,
                            'level': 'critical' if cpu_usage > 95 else 'warning',
                            'component': 'CPU',
                            'message': f"High CPU usage: {cpu_usage:.1f}%"
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
                ollama.list()
                self.ollama_available = True
                logging.info("Ollama client appears available.")
            except ImportError:
                 logging.info("Ollama package not installed (pip install ollama). Using local analysis.")
            except Exception as e:
                 logging.warning(f"Ollama connection error ({e}). Is the Ollama server running? Using local analysis.")


        self.analyses = deque(maxlen=10) # Keep fewer, more focused analyses
        # Removed visible_offset as we'll display based on available space


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
        """Create a concise summary string for the LLM."""
        try:
            summary = []
            if cpu := metrics.get('cpu'):
                summary.append(f"CPU: {cpu.get('Average Usage', 0):.1f}% avg")
            if mem := metrics.get('memory'):
                summary.append(f"Mem: {mem.get('Percentage', 0):.1f}% ({mem.get('Used', 'N/A')})")
                if mem.get('Swap Percentage', 0) > 10: # Only mention swap if significant
                     summary.append(f"Swap: {mem.get('Swap Percentage', 0):.1f}%")
            if gpus := metrics.get('gpu'):
                 gpu_summaries = []
                 for gpu in gpus:
                      name = gpu.get('Name', 'GPU').split(' ')[0] # Short name
                      load = gpu.get('Load', 'N/A')
                      mem = gpu.get('Memory Util', 'N/A')
                      temp = gpu.get('Temperature', 'N/A')
                      if load != 'N/A' or mem != 'N/A' or temp != 'N/A': # Only include if has data
                          gpu_summaries.append(f"{name}[Load:{load}, Mem:{mem}, Temp:{temp}]")
                 if gpu_summaries:
                     summary.append("GPU: " + ", ".join(gpu_summaries))

            if net := metrics.get('network'):
                 summary.append(f"Net: In {net.get('Recv Rate', '0bps')}, Out {net.get('Sent Rate', '0bps')}")

            if procs := metrics.get('processes'):
                 top_procs = [f"{p.get('Name', '?')}({p.get('CPU%', 0):.0f}%)" for p in procs[:3]]
                 summary.append(f"TopProcs: {', '.join(top_procs)}")

            return " | ".join(summary)

        except Exception as e:
            logging.error(f"Error preparing metrics summary: {str(e)}")
            return "Error preparing metrics data"

    def analyze(self, metrics: Dict, history: Dict) -> str:
        analysis = "Analysis unavailable." # Default
        try:
            metrics_summary = self._prepare_metrics_summary(metrics)
            prev_analyses = self.get_previous_analyses(2)
            prev_analyses_str = "\n".join([f"- {a}" for a in prev_analyses])

            prompt_context = f"""Previous insights:
{prev_analyses_str}

Current state summary:
{metrics_summary}

Identify the single most important NEW change or potential issue based *only* on the current state summary. Focus on high usage (CPU > 80%, Mem > 85%, GPU Load/Mem > 80%), significant network traffic changes, or top processes consuming resources. Avoid repeating previous insights. Be very concise (max 15 words). If stable, say so."""

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
                        model="llama3:8b", # Use a reasonably sized local model
                        messages=[{
                            "role": "user",
                            "content": prompt_context
                        }],
                        options={"temperature": 0.2, "num_predict": 30} # Limit output
                    )
                    analysis = response['message']['content'].strip()
                    logging.info(f"Ollama analysis: {analysis}")
                except Exception as e:
                    logging.error(f"Ollama API call failed: {e}")
                    analysis = self._generate_local_analysis(metrics) # Fallback
            else:
                # Fallback to simple rule-based analysis
                analysis = self._generate_local_analysis(metrics)
                logging.info(f"Local analysis: {analysis}")


            # Clean up the analysis text
            analysis = analysis.replace('\n', ' ').strip()
            if not analysis: # Handle empty LLM response
                 analysis = self._generate_local_analysis(metrics)

            # Store analysis with timestamp only if it's new or different from the last
            last_analysis = self.analyses[-1]['analysis'] if self.analyses else None
            if analysis != last_analysis:
                 self.analyses.append({
                    'timestamp': datetime.now(),
                    'analysis': analysis,
                 })
            # If same as last, return the last one but don't add duplicate to history
            elif last_analysis:
                 analysis = last_analysis


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

            return "System state normal."

        except Exception as e:
            logging.error(f"Error in local analysis: {str(e)}")
            return "Unable to generate local analysis."


class UIComponent:
    # Create table method adjusted to handle potentially missing keys gracefully
    def _create_table_from_dict(self, data: Dict, title: str, color: str = "white") -> Table:
        table = Table(show_header=False, box=box.ROUNDED, padding=(0, 1))
        table.add_column("Key", style=f"bold {color}", no_wrap=True)
        table.add_column("Value", style="white", no_wrap=True)
        for key, value in data.items():
            table.add_row(str(key), str(value))
        return table

    def _create_table_from_list(self, data: List[Dict], title: str, color: str = "white") -> Table:
        table = Table(box=box.ROUNDED, padding=(0, 1), show_header=True)

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
        if title.lower() == "top processes":
            headers = ["PID", "Name", "CPU%", "Memory%"] # Enforce order/presence
            col_styles.update({"PID": "cyan", "CPU%": "green", "Memory%": "magenta"})
            col_justify.update({"PID": "right", "CPU%": "right", "Memory%": "right"})
            col_width = {"PID": 7, "Name": None, "CPU%": 6, "Memory%": 8} # Let Name flex

        elif title.lower() == "gpu":
            headers = ["ID", "Name", "Load", "Memory Util", "Temperature"]
            col_styles.update({"ID": "cyan", "Load": "green", "Memory Util": "magenta", "Temperature": "red"})
            col_justify.update({"ID": "right", "Load": "right", "Memory Util": "right", "Temperature": "right"})
            col_width = {"ID": 3, "Name": None, "Load": 8, "Memory Util": 12, "Temperature": 8} # Let Name flex

        elif title.lower() == "disk":
             headers = ["Mountpoint", "Used", "Total", "Percentage"]
             col_styles.update({"Percentage": "yellow"})
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


    def generate_layout(self, current_metrics: Dict, alerts: List[Dict], analysis: str) -> Layout:
        # Check if GPU data exists and has useful information
        gpu_data = current_metrics.get('gpu', [])
        # GPU is "present" only if it has real data (not all N/A values)
        has_gpu = False
        if gpu_data:
            for gpu in gpu_data:
                # Check if at least one metric is not "N/A"
                if any(gpu.get(key) != "N/A" for key in ["Load", "Memory Util", "Temperature"]):
                    has_gpu = True
                    break

        # Create layout structure only once if it doesn't exist OR if GPU status changed
        if not self.current_layout or self._last_has_gpu != has_gpu:
            layout = Layout()
            layout.split_column(
                # Header: System Info (Static) - Render once?
                Layout(Panel(self.ui.create_displayable(
                            self.metrics_collector.system_info,
                            f"System: {self.metrics_collector.system_info.get('Node Name', '')}", "cyan"
                        ), box=box.MINIMAL), name="header", size=5),
                Layout(name="body"),
                # Footer: Status/Timestamp
                Layout(name="footer", size=1)
            )

            layout["body"].split_row(
                Layout(name="main", ratio=3),
                Layout(name="side", ratio=1)
            )

            layout["main"].split_column(
                Layout(name="top_row", ratio=1), # CPU, Mem
                Layout(name="middle_row", ratio=1), # Net, Disk
                Layout(name="bottom_row", ratio=2) # Processes (2/3) + Alerts (1/3)
            )
            layout["main"]["top_row"].split_row(Layout(name="cpu"), Layout(name="memory"))
            layout["main"]["middle_row"].split_row(Layout(name="network"), Layout(name="disk"))

            # Bottom row: Processes (2/3) and Alerts (1/3) side by side
            layout["main"]["bottom_row"].split_row(
                Layout(name="processes", ratio=2),
                Layout(name="alerts", ratio=1)
            )

            # Side: conditionally add GPU based on whether data exists
            if has_gpu:
                layout["side"].split_column(
                    Layout(name="gpu", ratio=1), # GPU Info/Status
                    Layout(name="analysis", ratio=2) # Analysis (extends vertically)
                )
            else:
                # No GPU: Analysis takes full height
                layout["side"].split_column(
                    Layout(name="analysis", ratio=1)
                )

            self.current_layout = layout
            self._last_has_gpu = has_gpu
        else:
             layout = self.current_layout

        # --- Update Panels with current data ---
        metrics = current_metrics # Alias for brevity

        # Header (already static) - maybe add uptime?

        # CPU
        layout["main"]["top_row"]["cpu"].update(
            Panel(self.ui.create_displayable(metrics.get('cpu',{}), "CPU", "green"),
                  title="CPU", box=box.ROUNDED, border_style="green")
        )
        # Memory
        layout["main"]["top_row"]["memory"].update(
             Panel(self.ui.create_displayable(metrics.get('memory',{}), "Memory", "magenta"),
                  title="Memory", box=box.ROUNDED, border_style="magenta")
        )
        # Network
        layout["main"]["middle_row"]["network"].update(
             Panel(self.ui.create_displayable(metrics.get('network',{}), "Network", "blue"),
                   title="Network", box=box.ROUNDED, border_style="blue")
        )
        # Disk
        layout["main"]["middle_row"]["disk"].update(
             Panel(self.ui.create_displayable(metrics.get('disk',[]), "Disk", "yellow"),
                  title="Disk", box=box.ROUNDED, border_style="yellow")
        )
        # Processes
        layout["main"]["bottom_row"]["processes"].update(
             Panel(self.ui.create_displayable(metrics.get('processes',[]), "Top Processes", "red"),
                   title="Processes", box=box.ROUNDED, border_style="red")
        )
        # GPU (only if GPU data exists)
        if has_gpu:
            layout["side"]["gpu"].update(
                Panel(self.ui.create_displayable(metrics.get('gpu',[]), "GPU", "bright_green"),
                      title="GPU", box=box.ROUNDED, border_style="bright_green")
            )

        # Analysis Panel
        # Determine available lines for analysis dynamically (estimate)
        console_height = console.height
        analysis_max_lines = max(5, console_height - 30) # Rough estimate of available lines
        visible_analyses = self.analyzer.get_visible_analyses(analysis_max_lines)

        analysis_renderables = []
        for item in visible_analyses:
            ts = Text(f"[{item['timestamp'].strftime('%H:%M:%S')}]", style="dim cyan")
            msg = Text(f" {item['analysis']}", style="white")
            analysis_renderables.append(Text.assemble(ts, msg))
            analysis_renderables.append(Text("")) # Spacer

        layout["side"]["analysis"].update(
            Panel(Group(*analysis_renderables) if analysis_renderables else Text("Waiting for analysis...", style="dim"),
                  title="Analysis", box=box.ROUNDED, border_style="white")
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
                  title="Alerts", box=box.ROUNDED, border_style="red")
        )

        # Footer
        layout["footer"].update(
             Text(f"Updated: {datetime.now():%H:%M:%S} | Refresh: {self.update_frequency}s | Log: system_monitor.log",
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
                analysis_interval = max(15, self.update_frequency * 3) # Analyze less frequently than metrics

                while True:
                    try:
                        collect_start = time.monotonic()

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
    args = parser.parse_args()

    if args.debug:
         logging.getLogger().setLevel(logging.DEBUG)
         logging.debug("Debug logging enabled.")

    # Check for sudo if on macOS for potentially better GPU info (informational)
    if platform.system() == 'Darwin' and os.geteuid() != 0:
         console.print("[yellow]INFO:[/yellow] Running without sudo on macOS. Real-time Apple Silicon GPU load/memory/temp might be unavailable.")


    # Initialize and run the monitor
    monitor = SystemMonitor(
        update_frequency=args.update_frequency,
        use_openai=args.use_openai,
        history_length=args.history_length # Pass history length
    )

    monitor.run()


if __name__ == "__main__":
    main()