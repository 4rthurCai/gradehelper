import subprocess
import logging
import os
import re


class Logger():
    _instance = None

    def __new__(cls, fileName="engr151GradeHelper.log", loggerName="myLogger"):
        if cls._instance is None:
            logger = logging.getLogger(loggerName)
            formatter = logging.Formatter(
                '[%(asctime)s][%(levelname)8s][%(filename)s %(lineno)3s]%(message)s'
            )
            logger.setLevel(logging.DEBUG)
            streamHandler = logging.StreamHandler()
            streamHandler.setFormatter(formatter)
            streamHandler.setLevel(logging.WARNING)
            fileHandler = logging.FileHandler(filename=fileName)
            fileHandler.setFormatter(formatter)
            fileHandler.setLevel(logging.DEBUG)
            logger.addHandler(fileHandler)
            logger.addHandler(streamHandler)
            cls._instance = logger
        return cls._instance


def first(iterable, condition=lambda x: True):
    try:
        return next(x for x in iterable if condition(x))
    except StopIteration:
        return None


def getProjRepoName(arg):
    id_, name, projNum, *_ = arg
    eng = re.sub('[\u4e00-\u9fa5]', '', name)
    eng = ''.join([word[0].capitalize() + word[1:] for word in eng.split()])
    return f"{eng}{id_}-p{projNum}"


def _remove_cpp_comments_and_strings(content):
    """Remove C++ comments (// and /* */) and string literals."""
    # First remove multi-line comments /* ... */
    content = re.sub(r'/\*.*?\*/', ' ', content, flags=re.DOTALL)

    # Remove single-line comments //
    lines = []
    for line in content.split('\n'):
        # Find // that's not in a string
        in_string = False
        string_char = None
        cleaned_line = []

        i = 0
        while i < len(line):
            char = line[i]

            if not in_string:
                # Check for string start
                if char in ['"', "'"]:
                    in_string = True
                    string_char = char
                    cleaned_line.append(' ')  # Replace string with space
                # Check for comment start
                elif i + 1 < len(line) and line[i:i+2] == '//':
                    break  # Rest of line is comment
                else:
                    cleaned_line.append(char)
            else:
                # In string
                if char == '\\' and i + 1 < len(line):
                    # Escaped character
                    cleaned_line.append(' ')
                    i += 1
                    cleaned_line.append(' ')
                elif char == string_char:
                    # End of string
                    in_string = False
                    cleaned_line.append(' ')
                else:
                    cleaned_line.append(' ')

            i += 1

        lines.append(''.join(cleaned_line))

    return '\n'.join(lines)


def passCodeQuality(path, language):
    """Check code quality and return (issue_count, issue_list).

    Returns:
        tuple: (int, list) - (number of issues, list of issue descriptions)
    """
    if language == "matlab":
        with open(path, encoding='utf-8', errors='replace') as f:
            content = f.read()

        issues = []

        # Check for global variables
        if ("global " in content) and ("Usage of global variables" not in issues):
            issues.append("Usage of global variables")

        # Check for while true
        if ("while true" in content) and ("Usage of 'while true'" not in issues):
            issues.append("Usage of 'while true'")

        # Check for if-elseif without final else
        if (not _check_if_else_structure(content)) and ("if-elseif structure without final else" not in issues):
            issues.append("if-elseif structure without final else")

        # Check for switch without otherwise
        if (not _check_switch_otherwise(content)) and ("switch structure without otherwise statement" not in issues):
            issues.append("switch structure without otherwise statement")

        # Check for input with message
        if (_has_input_with_message(content)) and ("input() with a given message (should separate prompt from input)" not in issues):
            issues.append("input() with a given message (should separate prompt from input)")

        # Check for sprintf+disp instead of fprintf
        if (_has_sprintf_disp_pattern(content)) and ("use sprintf() and then disp() instead of directly using fprintf()" not in issues):
            issues.append("use sprintf() and then disp() instead of directly using fprintf()")

        return (len(issues), issues)

    if language in ["c", "llvm-c"]:
        res = subprocess.check_output(
            ["ctags", "-x", "--sort=yes", path])
        lines = res.splitlines()
        # Parse ctags -x output: format is "name kind line_num file pattern..."
        # Only check lines where kind (second field) is exactly "variable"
        var_lines = []
        for line in lines:
            fields = line.split(None, 4)  # Split on whitespace, max 5 fields
            if len(fields) >= 4 and fields[1] == b"variable":
                var_lines.append(line)

        non_const_vars = [line for line in var_lines if b"const" not in line]
        if non_const_vars:
            return (len(non_const_vars), ["Non-const variables found"])
        return (0, [])

    if language in ["cc", "llvm-cc"]:
        issues = []

        # Check for using namespace
        with open(path, encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Remove comments and strings to avoid false positives
        cleaned_content = _remove_cpp_comments_and_strings(content)

        # Check for "using namespace" (case-sensitive)
        if re.search(r'\busing\s+namespace\s+', cleaned_content):
            issues.append("Usage of 'using namespace'")

        # Check for non-const variables using ctags
        res = subprocess.check_output(
            ["ctags", "-x", "--sort=yes", path])
        lines = res.splitlines()
        # Parse ctags -x output: format is "name kind line_num file pattern..."
        # Only check lines where kind (second field) is exactly "variable"
        var_lines = []
        for line in lines:
            fields = line.split(None, 4)  # Split on whitespace, max 5 fields
            if len(fields) >= 4 and fields[1] == b"variable":
                var_lines.append(line)

        non_const_vars = [line for line in var_lines if b"const" not in line]
        if non_const_vars:
            if "Non-const variables found" not in issues:
                issues.append("Non-const variables found")

        if issues:
            return (len(issues), issues)
        return (0, [])

    # No quality checks for other languages
    return (0, [])


def _check_if_else_structure(content):
    """Check if all if-elseif structures have a final else clause."""
    # Remove comments and strings to avoid false positives
    content = _remove_matlab_comments_and_strings(content)

    # Find all if blocks
    if_pattern = r'\bif\b'
    elseif_pattern = r'\belseif\b'
    else_pattern = r'\belse\b'
    # Match 'end' only when it's a statement keyword, not in array indexing
    # Must not be preceded by ( or { and not followed by operators or closing brackets
    # Also check it's either at start of line or after whitespace/semicolon
    end_pattern = r'(^|[\s;])\s*end\s*($|[;\s]|%)'
    # Patterns for structures that need 'end'
    block_start_pattern = r'\b(if|for|while|switch|function|parfor|spmd|try)\b'

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Found an if statement
        if re.search(if_pattern, line):
            has_elseif = False
            has_else = False
            depth = 1
            i += 1

            # Scan until matching end
            while i < len(lines) and depth > 0:
                inner_line = lines[i].strip()

                # Track nested blocks (if, for, while, switch, function, etc.)
                if re.search(block_start_pattern, inner_line):
                    depth += 1
                elif re.search(end_pattern, inner_line):
                    depth -= 1
                    if depth == 0:
                        break

                # At the same level as original if
                if depth == 1:
                    if re.search(elseif_pattern, inner_line):
                        has_elseif = True
                    elif re.search(else_pattern, inner_line):
                        has_else = True

                i += 1

            # If there's elseif but no final else, fail
            if has_elseif and not has_else:
                return False

        i += 1

    return True


def _check_switch_otherwise(content):
    """Check if all switch structures have an otherwise clause."""
    # Remove comments and strings
    content = _remove_matlab_comments_and_strings(content)

    switch_pattern = r'\bswitch\b'
    otherwise_pattern = r'\botherwise\b'
    end_pattern = r'\bend\b'

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Found a switch statement
        if re.search(switch_pattern, line):
            has_otherwise = False
            depth = 1
            i += 1

            # Scan until matching end
            while i < len(lines) and depth > 0:
                inner_line = lines[i].strip()

                # Track nested switch/end
                if re.search(switch_pattern, inner_line):
                    depth += 1
                elif re.search(end_pattern, inner_line):
                    depth -= 1
                    if depth == 0:
                        break

                # At the same level as original switch
                if depth == 1 and re.search(otherwise_pattern, inner_line):
                    has_otherwise = True

                i += 1

            # If no otherwise found, fail
            if not has_otherwise:
                return False

        i += 1

    return True


def _has_input_with_message(content):
    """Check if input() is used with a message argument."""
    # Remove comments and strings, but keep input calls
    content = _remove_matlab_comments_and_strings(content)

    # Pattern: input("message") or input('message')
    # Allow input() without arguments, disallow input with string argument
    pattern = r'\binput\s*\(\s*["\'][^"\']*["\']\s*\)'

    return bool(re.search(pattern, content))


def _has_sprintf_disp_pattern(content):
    """Check if sprintf+disp is used instead of fprintf."""
    # Remove comments and strings
    content = _remove_matlab_comments_and_strings(content)

    # Look for pattern: result = sprintf(...) followed by disp(result)
    # or variations like:
    # str = sprintf(...); disp(str)
    # message = sprintf(...); disp(message)

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check for sprintf assignment
        sprintf_match = re.search(r'(\w+)\s*=\s*sprintf\s*\(', line)
        if sprintf_match:
            var_name = sprintf_match.group(1)

            # Look ahead a few lines for disp(var_name)
            for j in range(i + 1, min(i + 5, len(lines))):
                next_line = lines[j].strip()
                disp_pattern = rf'\bdisp\s*\(\s*{re.escape(var_name)}\s*\)'
                if re.search(disp_pattern, next_line):
                    return True  # Found sprintf+disp pattern

        i += 1

    return False


def _remove_matlab_comments_and_strings(content):
    """Remove MATLAB comments and string literals."""
    # Remove single-line comments (%)
    lines = []
    for line in content.split('\n'):
        # Find % that's not in a string
        in_string = False
        string_char = None
        new_line = []

        for i, char in enumerate(line):
            if not in_string:
                if char in ["'", '"']:
                    in_string = True
                    string_char = char
                    new_line.append(' ')  # Replace string with space
                elif char == '%':
                    break  # Rest of line is comment
                else:
                    new_line.append(char)
            else:
                if char == string_char:
                    # Check for escaped quote
                    if i + 1 < len(line) and line[i + 1] == string_char:
                        new_line.append(' ')
                        continue
                    in_string = False
                new_line.append(' ')  # Replace string content with space

        lines.append(''.join(new_line))

    return '\n'.join(lines)


def getAllFiles(root):
    for f in os.listdir(root):
        if os.path.isfile(os.path.join(root, f)):
            yield os.path.join(f)
    dirs = [
        d for d in os.listdir(root)
        if os.path.isdir(os.path.join(root, d)) and d != ".git"
    ]
    for d in dirs:
        dirfiles = getAllFiles(os.path.join(root, d))
        for f in dirfiles:
            yield os.path.join(d, f)
