#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Setup script for engr151 Grade Helper

This script installs all necessary dependencies including Joint-Teapot.
"""

from setuptools import setup, find_packages
import os
import subprocess
import sys

def install_joint_teapot():
    """Install Joint-Teapot from the parent directory"""
    joint_teapot_path = os.path.join(os.path.dirname(__file__), '..', 'Joint-Teapot')

    if not os.path.exists(joint_teapot_path):
        print("❌ Error: Joint-Teapot not found at ../Joint-Teapot")
        print("Please clone Joint-Teapot first:")
        print("  cd ..")
        print("  git clone <joint-teapot-repo-url>")
        sys.exit(1)

    print(f"📦 Installing Joint-Teapot from {joint_teapot_path}...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", joint_teapot_path])
        print("✅ Joint-Teapot installed successfully")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install Joint-Teapot: {e}")
        sys.exit(1)

# Install Joint-Teapot first if running setup.py install
if 'install' in sys.argv or 'develop' in sys.argv:
    install_joint_teapot()

# Read README for long description
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="engr151-gradehelper",
    version="2.0.0",
    author="UMJI ENGR151 Teaching Team",
    description="Automated grading system for UMJI ENGR151 course",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/your-org/engr151-gradehelper",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.7',
    install_requires=[
        # Core dependencies
        'python-dotenv>=0.19.0',
        'GitPython>=3.1.0',

        # Joint-Teapot provides these dependencies:
        # - canvasapi
        # - requests
        # - mattermostdriver
        # - focs-gitea
        # - beautifulsoup4
        # - pydantic
        # - pydantic-settings
    ],
    extras_require={
        'dev': [
            'pytest>=6.0',
            'pytest-cov>=2.0',
            'black>=21.0',
            'flake8>=3.9',
            'mypy>=0.900',
        ],
    },
    entry_points={
        'console_scripts': [
            'engr151-grade=engr151GradeHelper:main',
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
