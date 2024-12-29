# code_extractor.py

def find_identifier(node, code):
    if node.type == 'identifier':
        return code[node.start_byte:node.end_byte].decode('utf-8')
    for child in node.children:
        name = find_identifier(child, code)
        if name is not None:
            return name
    return None

def node_text(node, code):
    return code[node.start_byte:node.end_byte].decode('utf-8')

def extract_parameters(node, code):
    params = []
    def walk(n):
        if n.type == 'parameter_declaration':
            ptext = node_text(n, code).strip()
            params.append(ptext)
        for c in n.children:
            walk(c)
    walk(node)
    return params

def extract_return_type_and_name(node, code):
    func_name = None
    return_type_parts = []
    parameters = []

    for child in node.children:
        if child.type in ('declarator', 'function_declarator', 'pointer_declarator'):
            func_name = find_identifier(child, code)
            parameters = extract_parameters(child, code)
        elif child.type in ('primitive_type', 'type_identifier', 'type_qualifier',
                            'storage_class_specifier', 'sized_type_specifier'):
            return_type_parts.append(node_text(child, code))

    if not func_name:
        for child in node.children:
            if child.type.endswith('declarator'):
                func_name = find_identifier(child, code)
                parameters = extract_parameters(child, code)
                break

    return_type = " ".join(return_type_parts).strip()
    if not return_type:
        return_type = "int"
    return func_name, return_type, parameters

def extract_functions_from_tree(root_node, code):
    functions = []
    def walk(node):
        if node.type == 'function_definition':
            func_name, return_type, parameters = extract_return_type_and_name(node, code)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            functions.append({
                'name': func_name,
                'return_type': return_type,
                'parameters': parameters,
                'start_line': start_line,
                'end_line': end_line,
                'prototype': False
            })
        for child in node.children:
            walk(child)
    walk(root_node)
    return functions

def extract_prototypes(root_node, code):
    prototypes = []
    def walk(node):
        if node.type == 'declaration':
            has_func_decl = False
            for c in node.children:
                if c.type in ('function_declarator', 'pointer_declarator'):
                    has_func_decl = True

            if has_func_decl:
                func_name, return_type, parameters = extract_return_type_and_name(node, code)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                if func_name:
                    prototypes.append({
                        'name': func_name,
                        'return_type': return_type,
                        'parameters': parameters,
                        'start_line': start_line,
                        'end_line': end_line,
                        'prototype': True
                    })
        for c in node.children:
            walk(c)
    walk(root_node)
    return prototypes

def find_typedef_name_in_node(node, code):
    if node.type == 'type_identifier':
        return node_text(node, code)
    for ch in node.children:
        name = find_typedef_name_in_node(ch, code)
        if name:
            return name
    return None

def extract_structs(root_node, code):
    parent_map = {}
    def build_parent_map(node, parent=None):
        parent_map[id(node)] = parent
        for ch in node.children:
            build_parent_map(ch, node)
    build_parent_map(root_node, None)

    def get_parent(node):
        return parent_map[id(node)]

    def extract_field_declaration(fd_node):
        field_type_parts = []
        field_names = []
        def parse_fd(n):
            if n.type in ('primitive_type', 'type_identifier', 'type_qualifier',
                          'storage_class_specifier', 'sized_type_specifier'):
                field_type_parts.append(node_text(n, code))
            elif n.type == 'identifier':
                field_names.append(node_text(n, code))
            for ch in n.children:
                parse_fd(ch)
        parse_fd(fd_node)
        field_type = " ".join(field_type_parts).strip()
        if not field_type:
            field_type = "int"
        return field_type, field_names

    def extract_struct_fields(fdl):
        fs = []
        def fw(n):
            if n.type == 'field_declaration':
                ftype, fnames = extract_field_declaration(n)
                for fn in fnames:
                    fs.append(f"{ftype} {fn}")
            for ch in n.children:
                fw(ch)
        fw(fdl)
        return fs

    def get_struct_name(struct_node):
        for c in struct_node.children:
            if c.type == 'type_identifier':
                return node_text(c, code)
        current = struct_node
        while current is not None:
            p = get_parent(current)
            if p is None:
                break
            if p.type in ('declaration', 'type_definition'):
                spec_found = False
                for ch in p.children:
                    if ch is struct_node:
                        spec_found = True
                    elif spec_found:
                        name = find_typedef_name_in_node(ch, code)
                        if name:
                            return name
            current = p
        return "<anonymous>"

    structs = []
    def walk(node):
        if node.type == 'struct_specifier':
            struct_code = node_text(node, code)
            fields = []
            struct_name = get_struct_name(node)
            for c in node.children:
                if c.type == 'field_declaration_list':
                    fields = extract_struct_fields(c)
            structs.append({
                'name': struct_name,
                'fields': fields,
                'code': struct_code
            })
        for c in node.children:
            walk(c)
    walk(root_node)
    return structs

def extract_typedefs(root_node, code):
    typedefs = []
    def walk(node):
        if node.type == 'declaration':
            decl_text = node_text(node, code)
            type_definition_found = False
            alias_found = False
            alias_name = None
            original_type_parts = []

            def parse_decl(n):
                nonlocal type_definition_found, alias_found, alias_name
                if n.type == 'storage_class_specifier' and node_text(n, code) == 'typedef':
                    type_definition_found = True
                elif n.type in ('primitive_type', 'type_identifier', 'type_qualifier', 'sized_type_specifier'):
                    if type_definition_found and not alias_found:
                        original_type_parts.append(node_text(n, code))
                elif n.type == 'identifier' and type_definition_found:
                    if not alias_found:
                        alias_name = node_text(n, code)
                        alias_found = True
                for ch in n.children:
                    parse_decl(ch)

            parse_decl(node)
            if type_definition_found and alias_found and alias_name:
                original_type = " ".join(original_type_parts).strip()
                if not original_type:
                    original_type = "<complex type>"
                typedefs.append({
                    'alias': alias_name,
                    'original': original_type,
                    'code': decl_text
                })
        for c in node.children:
            walk(c)
    walk(root_node)
    return typedefs

def extract_globals(root_node, code):
    fdefs = []
    def collect_fdefs(n):
        if n.type == 'function_definition':
            fdefs.append((n.start_byte, n.end_byte))
        for ch in n.children:
            collect_fdefs(ch)
    collect_fdefs(root_node)

    def is_inside_function_def(start_byte, end_byte):
        for (fs, fe) in fdefs:
            if start_byte >= fs and end_byte <= fe:
                return True
        return False

    globals_list = []
    def walk(node):
        if node.type == 'declaration':
            if not is_inside_function_def(node.start_byte, node.end_byte):
                var_type_parts = []
                var_names = []
                def parse_decl(n):
                    if n.type in ('primitive_type', 'type_identifier', 'type_qualifier',
                                  'storage_class_specifier', 'sized_type_specifier'):
                        var_type_parts.append(node_text(n, code))
                    elif n.type == 'identifier':
                        var_names.append(node_text(n, code))
                    for ch in n.children:
                        parse_decl(ch)
                parse_decl(node)
                var_type = " ".join(var_type_parts).strip()
                if not var_type:
                    var_type = "<unknown_type>"
                for vn in var_names:
                    globals_list.append({
                        'name': vn,
                        'type': var_type
                    })
        for c in node.children:
            walk(c)
    walk(root_node)
    return globals_list

def extract_function_calls(root_node, code):
    """
    Return a list of (caller_line, callee_name).
    We'll rely on the caller_line to map back to the caller function.
    """
    calls = []
    def walk(node):
        if node.type == 'call_expression':
            callee_name = None
            for ch in node.children:
                if ch.type == 'identifier':
                    callee_name = node_text(ch, code)
                    break
            caller_line = node.start_point[0] + 1
            if callee_name:
                calls.append((caller_line, callee_name))
        for c in node.children:
            walk(c)
    walk(root_node)
    return calls

def extract_info_from_file(parser, filename):
    with open(filename, 'rb') as f:
        code = f.read()
    tree = parser.parse(code)
    root_node = tree.root_node

    funcs = extract_functions_from_tree(root_node, code)
    structs = extract_structs(root_node, code)
    typedefs = extract_typedefs(root_node, code)
    globals_list = extract_globals(root_node, code)

    prototypes = []
    if filename.endswith('.h'):
        prototypes = extract_prototypes(root_node, code)

    all_functions = funcs + prototypes
    function_calls = extract_function_calls(root_node, code)

    return {
        'functions': all_functions,
        'structs': structs,
        'typedefs': typedefs,
        'globals': globals_list,
        'calls': function_calls
    }
