import pytest
import os
import shutil
from backend.services.intelligence.risk_analysis import RiskAnalyzer

@pytest.fixture
def temp_repo(tmpdir):
    repo_dir = str(tmpdir.mkdir("repo"))
    
    # Create a core model that is imported by many things
    os.makedirs(os.path.join(repo_dir, "backend", "models"))
    with open(os.path.join(repo_dir, "backend", "models", "user.py"), "w") as f:
        f.write("class User:\n    pass")
        
    # Create 4 services that import this model
    os.makedirs(os.path.join(repo_dir, "backend", "services"))
    for i in range(4):
        with open(os.path.join(repo_dir, "backend", "services", f"service_{i}.py"), "w") as f:
            f.write("from backend.models.user import User\n\ndef do_thing():\n    pass")
            
    # Create a leaf node (not imported by anything)
    with open(os.path.join(repo_dir, "backend", "services", "leaf.py"), "w") as f:
        f.write("def helper():\n    pass")
        
    return repo_dir

def test_risk_analyzer_leaf_node(temp_repo):
    analyzer = RiskAnalyzer(repo_path=temp_repo)
    impact = analyzer.analyze_change_impact(["backend/services/leaf.py"])
    
    assert impact['blast_radius_score'] == 0
    assert impact['risk_profile'] == 'LOW'
    assert len(impact['dependent_files']) == 0

def test_risk_analyzer_core_model(temp_repo):
    analyzer = RiskAnalyzer(repo_path=temp_repo)
    impact = analyzer.analyze_change_impact(["backend/models/user.py"])
    
    # It's imported by 4 services
    assert impact['blast_radius_score'] == 4
    # 4 dependents -> HIGH risk
    assert impact['risk_profile'] == 'HIGH'
    assert len(impact['dependent_files']) == 4

def test_risk_analyzer_security_override(temp_repo):
    analyzer = RiskAnalyzer(repo_path=temp_repo)
    impact = analyzer.analyze_change_impact(["backend/services/security.py"])
    
    # Even though 0 dependents, 'security' triggers CRITICAL
    assert impact['blast_radius_score'] == 0
    assert impact['risk_profile'] == 'CRITICAL'
