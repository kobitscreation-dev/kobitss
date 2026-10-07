import pytest
import json
from backend.services.intelligence.policy_engine import PolicyEngine
from backend.models.intelligence import TaskContract

def test_policy_engine_no_evidence():
    engine = PolicyEngine()
    contract = TaskContract(success_criteria_json='[]')
    
    is_valid, violations = engine.evaluate_task_contract(contract, [])
    assert not is_valid
    assert "No evidence provided" in violations[0]

def test_policy_engine_file_exists():
    engine = PolicyEngine()
    contract = TaskContract(
        success_criteria_json=json.dumps([{"type": "file_exists", "path": "test.py"}])
    )
    
    # Missing file
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "artifact", "name": "other.py"}])
    assert not is_valid
    assert len(violations) == 1
    
    # Existing file
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "artifact", "name": "test.py", "value": "print('hi')"}])
    assert is_valid
    assert len(violations) == 0

def test_policy_engine_ast_class_attr():
    engine = PolicyEngine()
    contract = TaskContract(
        success_criteria_json=json.dumps([
            {"type": "ast_class_attr", "path": "models.py", "class_name": "User", "attr_name": "email"}
        ])
    )
    
    # Missing attribute
    bad_code = "class User:\n    name = 'test'"
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "file_write", "path": "models.py", "content": bad_code}])
    assert not is_valid
    assert "missing attribute 'email'" in violations[0]
    
    # Has attribute (Assignment)
    good_code_assign = "class User:\n    email = 'test@test.com'"
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "file_write", "path": "models.py", "content": good_code_assign}])
    assert is_valid
    
    # Has attribute (Type Annotation)
    good_code_anno = "from typing import Mapped\nclass User:\n    email: Mapped[str]"
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "file_write", "path": "models.py", "content": good_code_anno}])
    assert is_valid

def test_policy_engine_ast_syntax_error():
    engine = PolicyEngine()
    contract = TaskContract(
        success_criteria_json=json.dumps([
            {"type": "ast_class_attr", "path": "models.py", "class_name": "User", "attr_name": "email"}
        ])
    )
    
    # Syntax error code
    bad_code = "class User:\n    email = "
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "file_write", "path": "models.py", "content": bad_code}])
    assert not is_valid
    assert "missing attribute" in violations[0]  # Failed to parse, so it didn't find the attribute

def test_policy_engine_regex():
    engine = PolicyEngine()
    contract = TaskContract(
        success_criteria_json=json.dumps([
            {"type": "regex_match", "path": "config.yaml", "pattern": "version:\\s*v2"}
        ])
    )
    
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "artifact", "path": "config.yaml", "value": "version: v1"}])
    assert not is_valid
    
    is_valid, violations = engine.evaluate_task_contract(contract, [{"type": "artifact", "path": "config.yaml", "value": "version: v2"}])
    assert is_valid
