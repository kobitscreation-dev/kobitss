import json
import ast
import re
from typing import Dict, Any, Tuple, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.intelligence import TaskContract


class ContractViolation(Exception):
    """Raised when a task fails its contract."""
    def __init__(self, message: str, violations: List[str]):
        super().__init__(message)
        self.violations = violations


class PolicyEngine:
    def __init__(self, db: AsyncSession = None):
        self.db = db
        
    def evaluate_task_contract(self, contract: TaskContract, evidence: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """
        Evaluates the evidence against the contract's success criteria.
        Returns (is_valid, list_of_violations).
        """
        if not evidence:
            return False, ["No evidence provided by the agent."]

        try:
            criteria = json.loads(contract.success_criteria_json) if contract.success_criteria_json else []
        except json.JSONDecodeError:
            return False, ["Invalid success_criteria_json format in contract."]

        if not isinstance(criteria, list):
            criteria = [criteria]

        violations = []

        # Build an evidence lookup map for easy access
        files_modified = {}
        test_results = {}
        for ev in evidence:
            if ev.get('type') == 'file_write' or ev.get('type') == 'artifact':
                # Sometimes agents pass path, sometimes name
                path = ev.get('path') or ev.get('name')
                if path:
                    files_modified[path] = ev.get('value', '') or ev.get('content', '')
            elif ev.get('type') == 'test_result':
                test_name = ev.get('name', 'default')
                test_results[test_name] = ev.get('success', False)

        # Evaluate each criterion
        for rule in criteria:
            rule_type = rule.get('type')
            
            if rule_type == 'file_exists':
                path = rule.get('path')
                if path not in files_modified:
                    violations.append(f"Missing required file modification: {path}")

            elif rule_type == 'regex_match':
                path = rule.get('path')
                pattern = rule.get('pattern')
                if path not in files_modified:
                    violations.append(f"Cannot run regex, file not modified: {path}")
                else:
                    content = files_modified[path]
                    if not re.search(pattern, content):
                        violations.append(f"File {path} does not match required pattern: {pattern}")

            elif rule_type == 'ast_class_attr':
                path = rule.get('path')
                class_name = rule.get('class_name')
                attr_name = rule.get('attr_name')
                
                if path not in files_modified:
                    violations.append(f"Cannot check AST, file not modified: {path}")
                    continue
                
                content = files_modified[path]
                if not self._check_ast_class_attr(content, class_name, attr_name):
                    violations.append(f"Class '{class_name}' in {path} is missing attribute '{attr_name}'.")

            elif rule_type == 'test_pass':
                test_name = rule.get('test_name', 'default')
                if test_name not in test_results:
                    violations.append(f"Missing required test result: {test_name}")
                elif not test_results[test_name]:
                    violations.append(f"Required test failed: {test_name}")
                    
            elif rule_type == 'ast_function_exists':
                path = rule.get('path')
                func_name = rule.get('func_name')
                
                if path not in files_modified:
                    violations.append(f"Cannot check AST, file not modified: {path}")
                    continue
                    
                content = files_modified[path]
                if not self._check_ast_func_exists(content, func_name):
                    violations.append(f"Function '{func_name}' not found in {path}.")

        if violations:
            return False, violations

        return True, []

    def _check_ast_class_attr(self, code: str, class_name: str, attr_name: str) -> bool:
        """Parses Python code and checks if a specific class has a specific attribute (assignment or annotation)."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
            
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                # Check body of the class
                for child in node.body:
                    # Check standard assignments: attr_name = ...
                    if isinstance(child, ast.Assign):
                        for target in child.targets:
                            if isinstance(target, ast.Name) and target.id == attr_name:
                                return True
                    # Check type annotations: attr_name: type
                    elif isinstance(child, ast.AnnAssign):
                        if isinstance(child.target, ast.Name) and child.target.id == attr_name:
                            return True
        return False
        
    def _check_ast_func_exists(self, code: str, func_name: str) -> bool:
        """Parses Python code and checks if a specific function definition exists."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
            
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return True
        return False

