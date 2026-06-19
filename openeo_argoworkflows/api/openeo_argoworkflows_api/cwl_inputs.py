"""Parse a CWL document's `inputs:` block into a schema for the Web Editor (#129).

API-side counterpart to the executor's #127 auto-wiring. The executor fills
`job_id`/`user_id`/`openeo_data` at run time; here we expose the same input
schema *before* submission so the editor can render a `context` form and flag
the inputs the backend fills automatically.

NOTE: this duplicates the pure parsing logic in the executor's
`extra_processes/process_implementations/cwl.py` (#127). The two images have
separate build contexts and cannot share a module today — keep them in sync.
Tracked as tech debt: unify into a shared package once a dev env exists.
"""

import urllib.request

import yaml

# CWL inputs the executor auto-fills from the openEO execution context (#127).
# The editor uses these flags to hide/grey the corresponding fields.
AUTO_FILLED_INPUTS = ("job_id", "user_id", "openeo_data")

# Cap remote CWL downloads so a hostile/huge URL can't exhaust memory.
_MAX_CWL_BYTES = 1 * 1024 * 1024  # 1 MiB


def fetch_cwl_text(url: str, timeout: int = 15) -> str:
    """Download a CWL document over http(s). Patched out in tests.

    Only http/https are allowed (basic SSRF guard — no file://, ftp://, etc.).
    """
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{scheme}': only http/https allowed")
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (scheme checked)
        return resp.read(_MAX_CWL_BYTES + 1).decode("utf-8")


def load_cwl_doc(text: str) -> dict:
    """Parse CWL text to a dict, resolving $graph packages to the run entry.

    For a $graph package, returns the tool/workflow the executor would run
    (the entry whose id is 'main' / '#main'), falling back to the first
    CommandLineTool/Workflow, then the first entry. Returns {} if the document
    can't be parsed into a mapping.
    """
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return {}
    if "$graph" in doc:
        entries = [e for e in (doc.get("$graph") or []) if isinstance(e, dict)]
        for entry in entries:
            if str(entry.get("id", "")).lstrip("#") == "main":
                return entry
        for entry in entries:
            if entry.get("class") in ("CommandLineTool", "Workflow", "ExpressionTool"):
                return entry
        return entries[0] if entries else {}
    return doc


def _input_is_optional(type_val) -> bool:
    """True if a CWL input type marks the input optional/nullable.

    Optional forms: a 'type?' shorthand, or a union list containing 'null'.
    """
    if isinstance(type_val, str):
        return type_val.endswith("?")
    if isinstance(type_val, list):
        return "null" in type_val
    return False


def parse_cwl_inputs(cwl_doc: dict) -> dict:
    """Normalise a CWL `inputs:` block (mapping or list form) to:

        {name: {type, default, has_default, optional, required}}

    Required = neither optional (nullable / '?' / null-union) nor defaulted.
    """
    inputs_block = (cwl_doc or {}).get("inputs")
    if isinstance(inputs_block, dict):
        items = list(inputs_block.items())
    elif isinstance(inputs_block, list):
        items = [(e.get("id"), e) for e in inputs_block if isinstance(e, dict)]
    else:
        return {}

    result = {}
    for name, spec in items:
        if not name:
            continue
        if isinstance(spec, dict):
            type_val = spec.get("type")
            has_default = "default" in spec
            default = spec.get("default")
        else:
            type_val = spec
            has_default = False
            default = None
        optional = _input_is_optional(type_val) or has_default
        result[name] = {
            "type": type_val,
            "default": default,
            "has_default": has_default,
            "optional": optional,
            "required": not optional,
        }
    return result


def build_input_schema(cwl_doc: dict) -> dict:
    """Parse inputs and flag the ones the executor auto-fills (#127).

    Returns {name: {..parse fields.., autofilled: bool}}.
    """
    parsed = parse_cwl_inputs(cwl_doc)
    for name, spec in parsed.items():
        spec["autofilled"] = name in AUTO_FILLED_INPUTS
    return parsed
