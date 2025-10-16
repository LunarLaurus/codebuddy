#!/usr/bin/env python3
import os
import stat
import subprocess

# Root of your repo (change if needed)
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))

# Iterate recursively
for dirpath, dirnames, filenames in os.walk(ROOT_DIR):
    for file in filenames:
        if file.endswith(".sh"):
            filepath = os.path.join(dirpath, file)
            st = os.stat(filepath)
            # Set +x for owner, group, others
            os.chmod(filepath, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            subprocess.run(["git", "update-index", "--chmod=+x", filepath])
            print(f"Git +x: {filepath}")