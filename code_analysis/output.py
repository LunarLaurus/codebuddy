# code_analysis/output.py
import json
from actions.summarizer import print_pretty_overview


def print_code_map(code_map, json_out=False, pretty=False, root_path="."):
    if json_out:
        print(json.dumps(code_map, indent=2))
    elif pretty:
        print_pretty_overview(code_map, root_path)
    else:
        print("[INFO] Done. Use --json or --pretty for output.")
