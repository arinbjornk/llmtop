# llmtop

`llmtop` is an intelligent system monitoring tool that combines real-time system metrics with LLM-powered insights. It provides a dynamic terminal interface showing system performance metrics enhanced with AI-driven analysis.

> **Note**: This project is currently in beta testing. Features and interfaces may change.

![llmtop Screenshot](screenshot.png)

## Features

- Real-time system metrics monitoring (CPU, Memory, Disk, Network)
- Process monitoring with resource usage
- AI-powered system analysis using either OpenAI or Ollama
- Smart alerting system for resource thresholds
- Dynamic terminal UI with auto-updating metrics

## Prerequisites

- Python 3.8+
- pip for package installation
- For Ollama integration: Ollama installed and running locally
- For OpenAI integration: OpenAI API key

## Quick Start

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/llmtop.git
   cd llmtop
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run llmtop:
   ```bash
   python llmtop.py
   ```

## Configuration

### Using with OpenAI

1. Set your OpenAI API key as an environment variable:
   ```bash
   export OPENAI_API_KEY='your-api-key-here'
   ```

2. Run with OpenAI integration:
   ```bash
   python llmtop.py --use-openai
   ```

### Using with Ollama

1. Ensure Ollama is installed and running locally
2. Run llmtop:
   ```bash
   python llmtop.py
   ```

## Command Line Options

```bash
python llmtop.py [OPTIONS]

Options:
  --update-frequency INTEGER  Update frequency in seconds (default: 5)
  --use-openai               Use OpenAI instead of local model
  --history-length INTEGER   Number of historical data points to keep (default: 60)
  --help                     Show this message and exit
```

## Known Issues

- Experimental support for Windows systems
- Update frequency might need adjustment on slower systems
- Some process names may be truncated in the display

## Contributing

This project is in beta, and we welcome contributions! Please feel free to:

- Report bugs
- Suggest features
- Submit pull requests

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

Built using:
- Rich for terminal UI
- OpenAI/Ollama for LLM integration
- psutil for system metrics
