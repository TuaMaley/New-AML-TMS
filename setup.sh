#!/bin/bash
echo "============================================"
echo " AML-TMS Platform - Setup"
echo "============================================"
echo ""
echo "Installing required Python packages..."
pip3 install scikit-learn>=1.3.0 numpy>=1.24.0 pandas>=2.0.0 python-docx>=0.8.11 openpyxl>=3.1.0 pypdf>=3.0.0
echo ""
echo "============================================"
echo " Setup complete! Run:  python3 start.py"
echo "============================================"
