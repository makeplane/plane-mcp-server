import ast
import os

def check_file_for_uuid(filepath):
    with open(filepath, "r") as f:
        tree = ast.parse(f.read())
        
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ('get_project', 'list_issues', 'create_issue_raw', 'update_issue_raw', 'delete_issue', 'list_states', 'create_state'):
            for arg in node.args.args:
                if arg.arg.endswith('_id'):
                    assert ast.unparse(arg.annotation) == "uuid.UUID", f"{arg.arg} in {node.name} is not UUID!"

def test_generated_core_type_hints():
    """Verify that the auto-generated core tools require UUIDs and proper types."""
    check_file_for_uuid(os.path.join(os.path.dirname(__file__), "../plane_mcp/tools/generated_core.py"))

def test_generated_metadata_type_hints():
    """Verify that the auto-generated metadata tools require UUIDs."""
    check_file_for_uuid(os.path.join(os.path.dirname(__file__), "../plane_mcp/tools/generated_metadata.py"))
