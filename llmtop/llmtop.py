from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.console import Group
from rich import box
import psutil
import platform
import GPUtil
import os
import time
from datetime import datetime
import argparse
from collections import deque
from typing import Dict, List, Any
import logging
import json


# Set up logging configuration at the top of the file
logging.basicConfig(
    level=logging.DEBUG,
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
            'processes': deque(maxlen=history_length)
        }
        self.system_info = self._get_system_info()
    
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
            cpu_freq = psutil.cpu_freq()
            cpu_percent = psutil.cpu_percent(interval=0.1, percpu=True)
            return {
                "Physical Cores": psutil.cpu_count(logical=False),
                "Total Cores": psutil.cpu_count(logical=True),
                "Current Frequency": f"{cpu_freq.current:.1f}MHz" if cpu_freq else "N/A",
                "Per Core Usage": cpu_percent,
                "Average Usage": sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0.0
            }
        except Exception as e:
            return {
                "Physical Cores": psutil.cpu_count(logical=False),
                "Total Cores": psutil.cpu_count(logical=True),
                "Current Frequency": "N/A",
                "Per Core Usage": [],
                "Average Usage": 0.0
            }
    
    def _get_memory_info(self) -> Dict:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "Total": f"{vm.total / (1024**3):.1f}GB",
            "Available": f"{vm.available / (1024**3):.1f}GB",
            "Used": f"{vm.used / (1024**3):.1f}GB",
            "Percentage": vm.percent,
            "Swap Used": f"{swap.used / (1024**3):.1f}GB",
            "Swap Percentage": swap.percent
        }
    
    def _get_disk_info(self) -> List[Dict]:
        disk_info = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                disk_info.append({
                    "Device": partition.device,
                    "Mountpoint": partition.mountpoint,
                    "Total": f"{usage.total / (1024**3):.1f}GB",
                    "Used": f"{usage.used / (1024**3):.1f}GB",
                    "Free": f"{usage.free / (1024**3):.1f}GB",
                    "Percentage": usage.percent
                })
            except PermissionError:
                continue
        return disk_info
    
    def _get_network_info(self) -> Dict:
        network = psutil.net_io_counters()
        return {
            "Bytes Sent": f"{network.bytes_sent / (1024**2):.1f}MB",
            "Bytes Received": f"{network.bytes_recv / (1024**2):.1f}MB",
            "Packets Sent": network.packets_sent,
            "Packets Received": network.packets_recv,
            "Error In": network.errin,
            "Error Out": network.errout
        }
    
    def _get_process_info(self, limit: int = 10) -> List[Dict]:
        """
        Get information about running processes, handling cases where CPU% might be None
        """
        processes = []
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    pinfo = proc.info
                    # Only add processes with valid CPU percentage
                    if pinfo['cpu_percent'] is not None:
                        processes.append({
                            "PID": pinfo['pid'],
                            "Name": pinfo['name'],
                            "CPU%": pinfo['cpu_percent'] or 0.0,  # Convert None to 0.0
                            "Memory%": pinfo['memory_percent'] or 0.0  # Convert None to 0.0
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                except Exception as e:
                    logging.warning(f"Error getting process info: {str(e)}")
                    continue

            # Only try to sort if we have processes
            if processes:
                # Filter out any remaining None values and sort
                valid_processes = [p for p in processes if p['CPU%'] is not None]
                return sorted(valid_processes, key=lambda x: x['CPU%'], reverse=True)[:limit]
            else:
                return []
                
        except Exception as e:
            logging.error(f"Error in _get_process_info: {str(e)}", exc_info=True)
            return []  # Return empty list on error
    
    def collect(self) -> Dict[str, Any]:
        logging.debug("Starting metrics collection")
        try:
            current_metrics = {
                'timestamp': datetime.now(),
                'cpu': self._get_cpu_info(),
                'memory': self._get_memory_info(),
                'disk': self._get_disk_info(),
                'network': self._get_network_info(),
                'processes': self._get_process_info()
            }
            logging.debug(f"Collected metrics: {current_metrics}")
            
            # Store in history
            for metric, value in current_metrics.items():
                if metric != 'timestamp':
                    self.history[metric].append({
                        'timestamp': current_metrics['timestamp'],
                        'data': value
                    })
            
            return current_metrics
        except Exception as e:
            logging.error(f"Error in collect: {str(e)}", exc_info=True)
            raise

class AlertManager:
    def __init__(self):
        self.thresholds = {
            'cpu_average': 80.0,
            'memory_percent': 90.0,
            'disk_percent': 85.0,
            'swap_percent': 60.0
        }
        self.alerts = deque(maxlen=100)  # Keep last 100 alerts
    
    def check_metrics(self, metrics: Dict) -> List[Dict]:
        logging.debug(f"Checking metrics: {metrics}")
        new_alerts = []
        
        try:
            # CPU alerts
            logging.debug("Checking CPU metrics")
            if 'cpu' in metrics:
                logging.debug(f"CPU metrics found: {metrics['cpu']}")
                if 'Average Usage' in metrics['cpu']:
                    cpu_usage = metrics['cpu']['Average Usage']
                    logging.debug(f"CPU usage: {cpu_usage}")
                    if cpu_usage > self.thresholds['cpu_average']:
                        new_alerts.append({
                            'level': 'critical' if cpu_usage > 90 else 'warning',
                            'component': 'CPU',
                            'message': f"High CPU usage: {cpu_usage:.1f}%"
                        })
            else:
                logging.warning("No CPU metrics found in data")
            
            # Memory alerts
            logging.debug("Checking memory metrics")
            if 'memory' in metrics:
                logging.debug(f"Memory metrics found: {metrics['memory']}")
                if 'Percentage' in metrics['memory']:
                    mem_usage = metrics['memory']['Percentage']
                    logging.debug(f"Memory usage: {mem_usage}")
                    if mem_usage > self.thresholds['memory_percent']:
                        new_alerts.append({
                            'level': 'critical' if mem_usage > 95 else 'warning',
                            'component': 'Memory',
                            'message': f"High memory usage: {mem_usage}%"
                        })
            else:
                logging.warning("No memory metrics found in data")
            
            # Disk alerts
            if 'disk' in metrics:
                for disk in metrics['disk']:
                    if disk['Percentage'] > self.thresholds['disk_percent']:
                        new_alerts.append({
                            'level': 'warning',
                            'component': 'Disk',
                            'message': f"High disk usage on {disk['Mountpoint']}: {disk['Percentage']}%"
                        })
            
            if new_alerts:
                for alert in new_alerts:
                    alert['timestamp'] = datetime.now()
                    self.alerts.append(alert)
            
            return new_alerts
            
        except Exception as e:
            logging.error(f"Error in check_metrics: {str(e)}", exc_info=True)
            return []

class SystemAnalyzer:
    def __init__(self, use_openai: bool = False):
        self.use_openai = use_openai
        self.client = None
        if use_openai:
            try:
                import openai
                self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            except ImportError:
                logging.warning("OpenAI package not installed. Please install with 'pip install openai'")
                self.use_openai = False
        
        self.analyses = deque(maxlen=100)  # Keep last 100 analyses
        self.visible_offset = 0  # Track where we are in the history


    def get_visible_messages(self, console_height: int) -> List[Dict]:
        """
        Get messages that should be visible based on console height.
        Now with added padding for complete message visibility.
        """
        messages = list(self.analyses)
        visible_messages = []
        current_height = 0
        # Add extra padding (2 lines) to target height to ensure complete visibility
        target_height = min(console_height - 6, 20)  # Increased padding from 4 to 6
        
        for msg in reversed(messages):  # Start from newest
            # Add a small buffer (+1) to line estimation for safety
            msg_lines = (len(msg['analysis']) // 50 + 2)  # Added +2 instead of +1
            if current_height + msg_lines > target_height:
                break
            visible_messages.insert(0, msg)  # Insert at start to maintain order
            current_height += msg_lines
        
        return visible_messages

    def _serialize_datetime(self, obj):
        """Helper method to serialize datetime objects"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)
        
    def get_previous_analyses(self, count: int = 2) -> List[str]:
        """Get the last N analyses, excluding the current one"""
        analyses_list = list(self.analyses)
        return [a['analysis'] for a in analyses_list[-count:]] if analyses_list else []

    def _prepare_metrics_string(self, metrics: Dict, history: Dict) -> str:
        try:
            # Create a simplified, clean string representation of the metrics
            current_metrics = {
                'CPU Usage': f"{metrics.get('cpu', {}).get('Average Usage', 0):.1f}%",
                'Memory Usage': f"{metrics.get('memory', {}).get('Percentage', 0):.1f}%",
                'Active Processes': len(metrics.get('processes', [])),
                'Network Packets': metrics.get('network', {}).get('Packets Received', 0),
                # 'Disk Usage': [
                #     f"{disk.get('Mountpoint')}: {disk.get('Percentage')}%"
                #     for disk in metrics.get('disk', [])
                #     if isinstance(disk, dict)
                # ],
                'Top Processes': [  # Add this new section
                    f"{proc.get('Name', 'Unknown')} (CPU: {proc.get('CPU%', 0):.1f}%, Mem: {proc.get('Memory%', 0):.1f}%)"
                    for proc in metrics.get('processes', [])[:3]  # Show top 3 processes
                ]
            }
            
            metrics_str = "Current System Metrics:\n"
            for key, value in current_metrics.items():
                if isinstance(value, list):
                    metrics_str += f"{key}:\n"
                    for item in value:
                        metrics_str += f"  - {item}\n"
                else:
                    metrics_str += f"{key}: {value}\n"
            
            return metrics_str
            
        except Exception as e:
            logging.error(f"Error preparing metrics string: {str(e)}")
            return "Error preparing metrics data"
    
    def analyze(self, metrics: Dict, history: Dict) -> str:
        try:
            # Prepare metrics string
            metrics_str = self._prepare_metrics_string(metrics, history)
            
            # Get previous analyses
            prev_analyses = self.get_previous_analyses(2)
            prev_analyses_str = "\n".join([f"Previous analysis {i+1}: {analysis}" 
                                         for i, analysis in enumerate(prev_analyses)])
            
            if self.use_openai and self.client:
                system_prompt = """You are a system monitoring assistant. Your role is to:
1. Analyze current system metrics and identify the most significant insight or issue
2. Avoid repeating the same insights from the previous two analyses
3. Focus on trends and changes rather than static states
4. Provide one short, specific statement about the most important finding
5. If no significant changes or issues are found, note system stability"""

                user_prompt = f"""Previous analyses:
{prev_analyses_str}

Current system data:
{metrics_str}

Generate ONE short statement (max 15 words) about the most significant NEW insight or change."""

                response = self.client.chat.completions.create(
                    model="gpt-4o-mini-2024-07-18",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    max_tokens=50,
                    temperature=0.3
                )
                analysis = response.choices[0].message.content
            else:
                # Try to use Ollama, fall back to local analysis if not available
                try:
                    import ollama
                    ollama_prompt = f"""Previous analyses:
{prev_analyses_str}

Current system data:
{metrics_str}
You are a system monitoring assistant. Your role is to:
1. Analyze current system metrics and identify the most significant insight or issue
2. Do not mention the same insights from the previous analysis
3. Focus on trends and changes in curren stystem data rather than static states. Disk usage is not important.
4. Provide one very short, specific statement about the most important finding"""

                    response = ollama.chat(
                        model="llama3.2",
                        messages=[{
                            "role": "user",
                            "content": ollama_prompt
                        }]
                    )
                    analysis = response['message']['content'].strip()
                except ImportError:
                    logging.warning("Ollama package not installed. Using local analysis.")
                    analysis = self._generate_local_analysis(metrics)
                except Exception as e:
                    logging.error(f"Ollama error: {str(e)}")
                    analysis = self._generate_local_analysis(metrics)

            # Clean up the analysis text
            analysis = analysis.replace('\n', ' ').strip()
            
            # Store analysis with timestamp
            self.analyses.append({
                'timestamp': datetime.now(),
                'analysis': analysis,
                'estimated_lines': len(analysis) // 50 + 1
            })
            
            return analysis
            
        except Exception as e:
            error_msg = f"Analysis Error: {str(e)}"
            logging.error(error_msg)
            self.analyses.append({
                'timestamp': datetime.now(),
                'analysis': error_msg
            })
            return error_msg

            # Clean up the analysis text
            analysis = analysis.replace('\n', ' ').strip()
            
            # Store analysis with timestamp
            self.analyses.append({
                'timestamp': datetime.now(),
                'analysis': analysis,
                'estimated_lines': len(analysis) // 50 + 1
            })
            
            return analysis
            
        except Exception as e:
            error_msg = f"Analysis Error: {str(e)}"
            logging.error(error_msg)
            self.analyses.append({
                'timestamp': datetime.now(),
                'analysis': error_msg
            })
            return error_msg

    def _generate_local_analysis(self, metrics: Dict) -> str:
        """Generate a simple analysis without using an LLM"""
        try:
            cpu_usage = metrics.get('cpu', {}).get('Average Usage', 0)
            memory_usage = metrics.get('memory', {}).get('Percentage', 0)
            process_count = len(metrics.get('processes', []))
            
            if cpu_usage > 80:
                return f"High CPU usage detected: {cpu_usage:.1f}%"
            elif memory_usage > 80:
                return f"High memory usage detected: {memory_usage:.1f}%"
            elif cpu_usage > 50 and memory_usage > 50:
                return f"Moderate system load: CPU {cpu_usage:.1f}%, Memory {memory_usage:.1f}%"
            else:
                return f"System running normally: {process_count} active processes"
        
        except Exception as e:
            logging.error(f"Error in local analysis: {str(e)}")
            return "Unable to generate analysis"

    def _generate_local_analysis(self, metrics: Dict) -> str:
        """Generate a simple analysis without using an LLM"""
        try:
            cpu_usage = metrics['cpu_usage']
            memory_usage = metrics['memory_usage']
            process_count = metrics['process_count']
            
            if cpu_usage > 80:
                return f"High CPU usage detected: {cpu_usage:.1f}%"
            elif memory_usage > 80:
                return f"High memory usage detected: {memory_usage:.1f}%"
            elif cpu_usage > 50 and memory_usage > 50:
                return f"Moderate system load: CPU {cpu_usage:.1f}%, Memory {memory_usage:.1f}%"
            else:
                return f"System running normally: {process_count} active processes"
        
        except Exception as e:
            logging.error(f"Error in local analysis: {str(e)}")
            return "Unable to generate analysis"

class UIComponent:
    def create_table(self, data: Dict, title: str, color: str = "white") -> Table:
        table = Table(show_header=False, box=box.ROUNDED, title=title, title_style=f"bold {color}")
        
        if isinstance(data, dict):
            table.add_column("Key", style=f"bold {color}")
            table.add_column("Value", style="white")
            for key, value in data.items():
                table.add_row(str(key), str(value))
        elif isinstance(data, list):
            if not data:  # Handle empty list case
                table.add_column("Status", style=f"bold {color}")
                table.add_row("No data available")
            else:
                # Check if this is the processes table
                if self._is_process_table(data):
                    return self._create_process_table(data, title, color)
                
                # Regular table handling
                headers = list(data[0].keys())
                table.show_header = True
                table.columns = []  # Reset columns
                for header in headers:
                    table.add_column(header, style=f"bold {color}")
                for item in data:
                    table.add_row(*[str(item[header]) for header in headers])
        
        return table

    def _is_process_table(self, data: List[Dict]) -> bool:
        """Check if the data structure matches process data"""
        if not data:
            return False
        expected_keys = {"PID", "Name", "CPU%", "Memory%"}
        return all(key in data[0] for key in expected_keys)

    def _create_process_table(self, data: List[Dict], title: str, color: str) -> Table:
        """Create a fixed-width table specifically for process data"""
        table = Table(
            title=title,
            title_style=f"bold {color}",
            box=box.ROUNDED,
            show_header=True,
            width=None,  # Let the table take the full width of its container
        )

        # Add columns with fixed widths
        table.add_column("PID", style=f"bold {color}", width=8, justify="right")
        table.add_column("Name", style=f"bold {color}", width=20, overflow="ellipsis")
        table.add_column("CPU%", style=f"bold {color}", width=8, justify="right")
        table.add_column("Memory%", style=f"bold {color}", width=10, justify="right")

        # Add rows with proper formatting
        for process in data:
            table.add_row(
                str(process['PID']),
                str(process['Name'])[:20],  # Truncate long names
                f"{process['CPU%']:.1f}",
                f"{process['Memory%']:.1f}"
            )

        return table

class SystemMonitor:
    def __init__(self, update_frequency: int = 5, use_openai: bool = False):
        self.update_frequency = update_frequency
        self.metrics_collector = SystemMetricsCollector()
        self.alert_manager = AlertManager()
        self.analyzer = SystemAnalyzer(use_openai)
        self.ui = UIComponent()
        
    def generate_layout(self, current_metrics: Dict, alerts: List[Dict], analysis: str) -> Layout:
        # Ensure all required keys exist with default values
        current_metrics = {
            'cpu': current_metrics.get('cpu', {
                "Physical Cores": "N/A",
                "Total Cores": "N/A",
                "Current Frequency": "N/A",
                "Per Core Usage": [],
                "Average Usage": 0.0
            }),
            'memory': current_metrics.get('memory', {
                "Total": "N/A",
                "Available": "N/A",
                "Used": "N/A",
                "Percentage": 0,
                "Swap Used": "N/A",
                "Swap Percentage": 0
            }),
            'network': current_metrics.get('network', {
                "Bytes Sent": "0MB",
                "Bytes Received": "0MB",
                "Packets Sent": 0,
                "Packets Received": 0,
                "Error In": 0,
                "Error Out": 0
            }),
            'disk': current_metrics.get('disk', []),
            'processes': current_metrics.get('processes', [])
        }

        # Initialize main layout
        layout = Layout()
        
        # Main layout structure
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=8),
            Layout(name="footer", size=3)
        )
        
        # Body layout
        layout["body"].split_row(
            Layout(name="metrics", ratio=2),
            Layout(name="sidebar", ratio=1)
        )
        
        # Metrics layout
        layout["metrics"].split_column(
            Layout(name="system", ratio=2),
            Layout(name="processes", ratio=1)
        )
        
        # System metrics layout
        layout["system"].split_row(
            Layout(name="performance", ratio=1),
            Layout(name="resources", ratio=1)
        )
        
        # Header with system information
        layout["header"].update(
            Panel(
                self.ui.create_table(
                    self.metrics_collector.system_info,
                    "System Information",
                    "cyan"
                ),
                box=box.ROUNDED
            )
        )
        
        # Performance metrics (CPU & Network)
        perf_layout = Layout()
        perf_layout.split_column(
            Layout(name="cpu"),
            Layout(name="network")
        )
        perf_layout["cpu"].update(Panel(
            self.ui.create_table(current_metrics['cpu'], "CPU", "green"),
            box=box.ROUNDED
        ))
        perf_layout["network"].update(Panel(
            self.ui.create_table(current_metrics['network'], "Network", "blue"),
            box=box.ROUNDED
        ))
        layout["metrics"]["system"]["performance"].update(perf_layout)
        
        # Resource metrics (Memory & Disk)
        res_layout = Layout()
        res_layout.split_column(
            Layout(name="memory"),
            Layout(name="disk")
        )
        res_layout["memory"].update(Panel(
            self.ui.create_table(current_metrics['memory'], "Memory", "magenta"),
            box=box.ROUNDED
        ))
        res_layout["disk"].update(Panel(
            self.ui.create_table(current_metrics['disk'], "Disk", "yellow"),
            box=box.ROUNDED
        ))
        layout["metrics"]["system"]["resources"].update(res_layout)
        
        # Processes
        layout["metrics"]["processes"].update(Panel(
            self.ui.create_table(current_metrics['processes'], "Top Processes", "red"),
            box=box.ROUNDED
        ))
        
        # Sidebar (Alerts & Analysis)
        sidebar = Layout()
        sidebar.split_column(
            Layout(name="alerts", size=10),
            Layout(name="analysis", ratio=1)  # Give analysis more space
        )
        
        # Format alerts
        alert_text = "\n".join([
            f"[{a['level']}] {a['timestamp'].strftime('%H:%M:%S')} - {a['message']}"
            for a in list(self.alert_manager.alerts)[-5:]  # Show last 5 alerts
        ]) if alerts else "No active alerts"
        
        sidebar["alerts"].update(Panel(
            Text(alert_text, style="bold red"),
            title="Alerts",
            box=box.ROUNDED
        ))
        
        # Get console dimensions for dynamic sizing
        console = Console()
        _, console_height = console.size
        # Add extra padding to panel height
        analysis_panel_height = min(console_height - 13, 22)  # Adjusted from -15 to -13 and max height from 20 to 22
        
        # Get messages that will fit
        visible_messages = self.analyzer.get_visible_messages(analysis_panel_height)
        

        text_elements = []
        for item in visible_messages:
            timestamp = Text(f"[{item['timestamp'].strftime('%H:%M:%S')}] ", style="bold cyan")
            message = Text(item['analysis'], style="yellow", overflow="fold")
            combined = Text.assemble(timestamp, message)
            text_elements.append(combined)
            text_elements.append(Text(""))  # Add an empty line between messages

        analysis_text = Group(*text_elements) if text_elements else Text("No analysis available")
        
        sidebar["analysis"].update(Panel(
            analysis_text,
            title="LLM Analysis",
            box=box.ROUNDED,
            height=analysis_panel_height + 2  # Add padding to panel height
        ))
        
        layout["body"]["sidebar"].update(sidebar)
        
        
        # Footer
        layout["footer"].update(Panel(
            Text(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                 f"Refresh: {self.update_frequency}s",
                 style="bold white"),
            box=box.ROUNDED
        ))
        
        return layout
    
    def run(self):
        analysis_text = "Starting analysis..."
        logging.info("Starting system monitor")
        
        # Create initial metrics structure with empty values
        initial_metrics = {
            'cpu': {
                "Physical Cores": 0,
                "Total Cores": 0,
                "Current Frequency": "N/A",
                "Per Core Usage": [],
                "Average Usage": 0.0
            },
            'memory': {
                "Total": "0GB",
                "Available": "0GB",
                "Used": "0GB",
                "Percentage": 0,
                "Swap Used": "0GB",
                "Swap Percentage": 0
            },
            'disk': [{
                "Device": "N/A",
                "Mountpoint": "N/A",
                "Total": "0GB",
                "Used": "0GB",
                "Free": "0GB",
                "Percentage": 0
            }],
            'network': {
                "Bytes Sent": "0MB",
                "Bytes Received": "0MB",
                "Packets Sent": 0,
                "Packets Received": 0,
                "Error In": 0,
                "Error Out": 0
            },
            'processes': []
        }
        
        try:
            with Live(
                self.generate_layout(initial_metrics, [], analysis_text),
                refresh_per_second=4,
                screen=True
            ) as live:
                while True:
                    try:
                        # Collect metrics
                        logging.debug("Collecting metrics...")
                        current_metrics = self.metrics_collector.collect()
                        logging.debug(f"Current metrics: {current_metrics}")
                        
                        # Check for alerts
                        logging.debug("Checking alerts...")
                        alerts = self.alert_manager.check_metrics(current_metrics)
                        logging.debug(f"Current alerts: {alerts}")
                        
                        # Generate analysis
                        logging.debug("Generating analysis...")
                        analysis = self.analyzer.analyze(
                            current_metrics,
                            self.metrics_collector.history
                        )
                        
                        # Update the live display
                        live.update(
                            self.generate_layout(
                                current_metrics,
                                alerts,
                                analysis
                            )
                        )
                        
                        # Wait for next update
                        time.sleep(self.update_frequency)
                        
                    except KeyboardInterrupt:
                        logging.info("Keyboard interrupt received")
                        console.print("[bold red]Monitoring stopped.[/bold red]")
                        break
                    except Exception as e:
                        logging.error(f"Error in main loop: {str(e)}", exc_info=True)
                        console.print(f"[bold red]Error: {str(e)}[/bold red]")
                        time.sleep(1)  # Prevent rapid error loops
        except Exception as e:
            logging.error(f"Fatal error in run method: {str(e)}", exc_info=True)
            raise

def main():
    parser = argparse.ArgumentParser(description="Enhanced System Monitor")
    parser.add_argument(
        "--update-frequency",
        type=int,
        default=5,
        help="Update frequency in seconds (default: 5)"
    )
    parser.add_argument(
        "--use-openai",
        action="store_true",
        help="Use OpenAI instead of local model"
    )
    parser.add_argument(
        "--history-length",
        type=int,
        default=60,
        help="Number of historical data points to keep (default: 60)"
    )
    args = parser.parse_args()

    # Initialize and run the monitor
    monitor = SystemMonitor(
        update_frequency=args.update_frequency,
        use_openai=args.use_openai
    )
    
    try:
        monitor.run()
    except Exception as e:
        console.print(f"[bold red]Fatal error: {str(e)}[/bold red]")

if __name__ == "__main__":
    main()