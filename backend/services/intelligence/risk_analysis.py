import os
import ast
from typing import Dict, Any, List, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.intelligence import ProcessMemory

class RiskAnalyzer:
    def __init__(self, db: AsyncSession = None, repo_path: str = "."):
        self.db = db
        self.repo_path = repo_path

    async def predict_rework_risk(self, task_category: str) -> float:
        if not self.db:
            return 0.0
        stmt = select(ProcessMemory).where(ProcessMemory.task_category == task_category)
        result = await self.db.execute(stmt)
        memory = result.scalars().first()
        if memory:
            return memory.avg_corrections
        return 0.0

    def analyze_change_impact(self, modified_files: List[str]) -> Dict[str, Any]:
        """
        Dynamically calculates the blast radius of changes using AST parsing.
        Finds how many other files in the repo import the modules being modified.
        """
        impact_map = {
            'files': modified_files,
            'dependent_files': [],
            'blast_radius_score': 0,
            'risk_profile': 'LOW'
        }
        
        if not modified_files:
            return impact_map

        # 1. Identify module names being modified
        modified_modules = set()
        for f in modified_files:
            if f.endswith('.py'):
                # backend/models/user.py -> backend.models.user
                mod_name = f.replace('/', '.').replace('\\\\', '.').replace('.py', '')
                modified_modules.add(mod_name)
                # Also track base name just in case of relative imports
                modified_modules.add(os.path.basename(f).replace('.py', ''))

        # 2. Scan entire repository for imports of these modules
        dependents = set()
        
        for root, dirs, files in os.walk(self.repo_path):
            # Prune virtual environments, sandboxes, git, and caches in-place
            dirs[:] = [
                d for d in dirs
                if d not in ('venv', '.venv', '__pycache__', '.git', 'sandboxes', 'node_modules', '.pytest_cache')
            ]
                
            for file in files:
                if file.endswith('.py'):
                    full_path = os.path.join(root, file)
                    # Don't check the files we are actively modifying
                    rel_path = os.path.relpath(full_path, self.repo_path).replace('\\\\', '/')
                    if rel_path in modified_files:
                        continue
                        
                    # Parse and find imports
                    try:
                        with open(full_path, 'r', encoding='utf-8') as code_file:
                            content = code_file.read()
                        tree = ast.parse(content)
                    except (SyntaxError, UnicodeDecodeError):
                        continue
                        
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if any(m in alias.name for m in modified_modules):
                                    dependents.add(rel_path)
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and any(m in node.module for m in modified_modules):
                                dependents.add(rel_path)

        impact_map['dependent_files'] = sorted(dependents)[:15]
        impact_map['blast_radius_score'] = len(dependents)
        
        # 3. Determine Risk Profile
        if len(dependents) > 10:
            impact_map['risk_profile'] = 'CRITICAL'
        elif len(dependents) > 3:
            impact_map['risk_profile'] = 'HIGH'
        elif len(dependents) > 0:
            impact_map['risk_profile'] = 'MEDIUM'
            
        # Hardcoded overrides for security/db
        for f in modified_files:
            lower_f = f.lower()
            if 'auth' in lower_f or 'security' in lower_f or 'crypto' in lower_f:
                impact_map['risk_profile'] = 'CRITICAL'
            if 'database' in lower_f or 'models/' in lower_f:
                if impact_map['risk_profile'] in ['LOW', 'MEDIUM']:
                    impact_map['risk_profile'] = 'HIGH'

        return impact_map

