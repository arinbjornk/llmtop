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

## Installation

Install directly from PyPI:
```bash
pip install llmtop
```

## LLM Backend Options

llmtop supports two LLM backends for system analysis:

### OpenAI (GPT-4o mini)
1. Set your OpenAI API key:
   ```bash
   export OPENAI_API_KEY='your-api-key-here'
   ```
2. Run with OpenAI:
   ```bash
   llmtop --use-openai
   ```

### Ollama (Default)
1. Install Ollama from [ollama.ai](https://ollama.ai)
2. Start the Ollama service
3. Run llmtop:
   ```bash
   llmtop
   ```

## Command Line Options

```bash
llmtop [OPTIONS]

Options:
  --update-frequency INTEGER  Update frequency in seconds (default: 5)
  --use-openai               Use OpenAI instead of local model
  --history-length INTEGER   Number of historical data points to keep (default: 60)
  --help                     Show this message and exit
```

## Usage Tips

- The tool defaults to using Ollama for analysis, which is free but requires local installation
- OpenAI mode provides more detailed analysis but requires an API key and has associated costs
- Adjust update frequency based on your system's performance and monitoring needs

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
