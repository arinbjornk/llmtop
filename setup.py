from setuptools import setup, find_packages

setup(
    name="llmtop",
    version="0.0.1",
    author="Your Name",
    description="LLM-powered system monitoring with real-time performance insights",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
    ],
    python_requires=">=3.8",
)

# llmtop/__init__.py
"""LLM-powered system monitoring"""
__version__ = "0.0.1"
