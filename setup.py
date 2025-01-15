from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="llmtop",
    version="0.1.0",
    author="Arinbjörn Kolbeinsson",
    description="LLM-powered system monitoring with real-time performance insights",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
        "Topic :: System :: Monitoring",
    ],
    python_requires=">=3.8",
    install_requires=[
        "rich>=13.7.0",
        "psutil>=5.9.0",
        "GPUtil>=1.4.0",
        "openai>=1.12.0",
        "ollama>=0.1.6",
        "python-dateutil>=2.8.2",
        "typing-extensions>=4.9.0"
    ],
    entry_points={
        'console_scripts': [
            'llmtop=llmtop.llmtop:main',
        ],
    },
)