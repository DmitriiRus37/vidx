"""Download a GigaAM model into its cache once (vidx setup), so the first run does not.

usage: gigaam_warm.py <model>      e.g. v3_e2e_rnnt; run by .venv-giga's Python with -I -X utf8 (setup.isolated)
"""
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves this script's directory out
    from common import unix_stderr

    unix_stderr()
    import gigaam

    gigaam.load_model(sys.argv[1], device="cpu")
