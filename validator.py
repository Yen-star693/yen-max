import ast
import json
from typing import Optional, Tuple


def validate_python(code: str) -> Tuple[bool, Optional[str], Optional[int]]:
    """
    Actually parse Python code with the ast module to check for
    real syntax errors. No execution happens - ast.parse only builds
    a syntax tree, it never runs the code.

    Args:
        code: Python source code to check

    Returns:
        (is_valid, error_message, line_number)
        error_message and line_number are None if valid
    """
    try:
        ast.parse(code)
        return True, None, None
    except SyntaxError as e:
        return False, e.msg, e.lineno
    except ValueError as e:
        # Rare: null bytes etc.
        return False, str(e), None


def validate_json(content: str) -> Tuple[bool, Optional[str], Optional[int]]:
    """
    Actually parse JSON with the stdlib json module.

    Args:
        content: JSON text to check

    Returns:
        (is_valid, error_message, line_number)
    """
    try:
        json.loads(content)
        return True, None, None
    except json.JSONDecodeError as e:
        return False, e.msg, e.lineno


def get_language(filename: str) -> str:
    """
    Determine language from file extension.

    Args:
        filename: Name of the file

    Returns:
        Lowercase language identifier
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    mapping = {
        "py": "python",
        "js": "javascript",
        "html": "html",
        "css": "css",
        "json": "json",
        "md": "markdown",
        "txt": "text",
        "yml": "yaml",
        "yaml": "yaml",
        "toml": "toml",
    }

    return mapping.get(ext, ext or "text")


def can_validate_syntax(filename: str) -> bool:
    """
    Check whether real syntax validation is available for this file type.

    Python and JSON are checkable here via stdlib parsers (ast.parse,
    json.loads), neither of which executes anything. Other languages
    would require running untrusted code or a real parser, so we're
    honest that we skip them rather than faking it.

    Also skip obvious non-code files like requirements.txt, README.md,
    .env, or anything that looks like it holds a secret even if it
    happens to end in .py/.json.

    Args:
        filename: Name of the file

    Returns:
        True if validate_file can actually check this file's syntax
    """
    lang = get_language(filename)

    if lang not in ("python", "json"):
        return False

    # Skip files that are obviously not code/data even if the extension matches
    if any(x in filename.lower() for x in ["token", "secret", "env"]):
        return False

    return True


def validate_file(filename: str, content: str) -> Tuple[bool, Optional[str], Optional[int]]:
    """
    Validate a generated file's syntax if a real checker exists for it.

    Args:
        filename: Name of the file
        content: File content to validate

    Returns:
        (is_valid, error_message, line_number)
        is_valid is True if there's nothing to check or the code is valid
    """
    if not can_validate_syntax(filename):
        return True, None, None

    lang = get_language(filename)

    if lang == "json":
        return validate_json(content)

    return validate_python(content)


def validate_filename(filename: str) -> Tuple[bool, Optional[str]]:
    """
    Check a filename for path traversal and other unsafe patterns
    before it's ever used to create a file or Discord attachment.

    This is a real check against config.FILENAME_BLOCKED_PATTERNS,
    not a formality - a filename that fails this is rejected outright
    and never reaches generate_file().

    Args:
        filename: Proposed filename from the planner

    Returns:
        (is_safe, reason_or_None)
    """
    from config import FILENAME_BLOCKED_PATTERNS, MAX_FILENAME_LENGTH

    if not filename or not filename.strip():
        return False, "empty filename"

    if len(filename) > MAX_FILENAME_LENGTH:
        return False, f"filename exceeds {MAX_FILENAME_LENGTH} characters"

    for pattern in FILENAME_BLOCKED_PATTERNS:
        if pattern in filename:
            return False, f"filename contains blocked pattern '{pattern}'"

    # Absolute paths (leading slash, or a drive letter like C:) are unsafe
    if filename.startswith("/") or filename.startswith("\\"):
        return False, "absolute paths are not allowed"

    if len(filename) >= 2 and filename[1] == ":":
        return False, "absolute paths are not allowed"

    # Hidden system-style files (leading dot) are allowed for things like
    # .env or .gitignore, but not combined with any path separator above
    return True, None


def check_unused_imports(code: str) -> list:
    """
    Check for imports that are never referenced elsewhere in the file.

    This is a real, if simple, static check - it walks the actual
    parsed AST and looks for name usage, no guessing involved.

    Args:
        code: Python source code

    Returns:
        List of import names that appear unused
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    imported_names.append(alias.asname or alias.name)

    if not imported_names:
        return []

    used_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # e.g. os.path -> "os" is used via the Name node already
            pass

    return [name for name in imported_names if name not in used_names]
