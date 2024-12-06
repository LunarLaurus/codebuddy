import os
from code_extractor import extract_info_from_file

def is_source_file(filename):
    # Include .c and .h files
    return filename.endswith('.c') or filename.endswith('.h')

def build_code_map(parser, root_path):
    code_map = {}
    for dirpath, dirnames, filenames in os.walk(root_path):
        for fname in filenames:
            if is_source_file(fname):
                full_path = os.path.join(dirpath, fname)
                info = extract_info_from_file(parser, full_path)
                code_map[full_path] = info
    return code_map
