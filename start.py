#!/usr/bin/env python3
"""
AML-TMS Platform — Start Script
Automatically installs missing dependencies before starting the server.
"""
import sys, os, time, subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

def auto_install():
    """Install any missing required packages."""
    required = {
        'sklearn':    'scikit-learn>=1.3.0',
        'numpy':      'numpy>=1.24.0',
        'pandas':     'pandas>=2.0.0',
        'docx':       'python-docx>=0.8.11',
        'openpyxl':   'openpyxl>=3.1.0',
        'pypdf':      'pypdf>=3.0.0',
    }
    missing = []
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"\n  Installing {len(missing)} missing package(s):", flush=True)
        for pkg in missing:
            print(f"    pip install {pkg} ...", flush=True)
        cmd = [sys.executable, '-m', 'pip', 'install', '--quiet'] + missing
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  ✓ All packages installed successfully.\n", flush=True)
        else:
            print(f"  ⚠ Some packages failed to install:\n{result.stderr[:500]}", flush=True)
            print(f"  Tip: Run:  pip install {' '.join(missing)}", flush=True)
    else:
        pass  # All packages present — silent

if __name__ == '__main__':
    auto_install()
    port = int(os.environ.get('PORT', 8787))
    # Auto-restart loop
    while True:
        try:
            from api_server import run
            run(port)
        except KeyboardInterrupt:
            print("\n  Server stopped.")
            break
        except Exception as e:
            print(f"  Server error: {e} — restarting in 2s...", flush=True)
            time.sleep(2)
            import importlib
            if 'api_server' in sys.modules:
                del sys.modules['api_server']
