from typing import List, Dict
import os

class WorktreeManager:
    def __init__(self, sandbox_manager):
        self.sandbox_manager = sandbox_manager
        
    def detect_conflicts(self, worktree_paths: List[str]) -> Dict[str, List[str]]:
        # Simplified conflict detection for mock purposes
        # Checks if two worktrees touched the same file
        file_modifications = {}
        conflicts = {'same_file_edits': []}
        
        for idx, path in enumerate(worktree_paths):
            if os.path.exists(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        rel_path = os.path.relpath(os.path.join(root, file), path)
                        if rel_path not in file_modifications:
                            file_modifications[rel_path] = []
                        file_modifications[rel_path].append(idx)
                        
        for file, agents in file_modifications.items():
            if len(agents) > 1:
                conflicts['same_file_edits'].append(file)
                
        return conflicts
