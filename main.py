import sys
import json
import argparse
from parser_setup import load_language, create_parser
from code_map_builder import build_code_map
from summarizer import summarize_files, summarize_project, create_overview_text, refine_file_summaries, print_pretty_overview

def main():
    parser = argparse.ArgumentParser(description="Generate a verbose summary of a C codebase with two LLM passes.")
    parser.add_argument("--path", required=True, help="Path to the root of the codebase.")
    parser.add_argument("--json", action="store_true", help="Output the code map as JSON instead of human-readable summary.")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM-based summaries entirely.")
    parser.add_argument("--only-first-llm", action="store_true", help="Run only the first LLM pass, skip the second refinement pass.")
    parser.add_argument("--pretty", action="store_true", help="Use rich formatting for a prettier output.")
    args = parser.parse_args()

    language = load_language()
    ts_parser = create_parser(language)

    code_map = build_code_map(ts_parser, args.path)

    if args.no_llm:
        # No LLM at all
        if args.json:
            print(json.dumps(code_map, indent=2))
        else:
            if args.pretty:
                print_pretty_overview(code_map, args.path)
            else:
                overview = create_overview_text(code_map, args.path)
                print(overview)
        return

    # First LLM pass: initial summaries
    file_summaries = summarize_files(code_map)

    # Summarize the project after first pass
    project_summary = summarize_project(file_summaries)
    code_map['_project_summary'] = project_summary

    if not args.only_first_llm:
        # Second LLM pass: refine file summaries
        refine_file_summaries(code_map)

        # After refinement, overwrite file_summary with the refined version
        for fname, info in code_map.items():
            if not isinstance(info, dict):
                continue
            refined = info.pop('file_refined_summary', None)
            if refined:
                info['file_summary'] = refined

    # Now file_summary always contains the final summary
    if args.json:
        print(json.dumps(code_map, indent=2))
    else:
        if args.pretty:
            print_pretty_overview(code_map, args.path)
        else:
            overview = create_overview_text(code_map, args.path)
            print(overview)

if __name__ == "__main__":
    sys.exit(main())
