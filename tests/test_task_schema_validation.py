import pytest
from pydantic import ValidationError
from backend.services.intelligence.task_parser import parse_and_validate_tasks

def test_valid_decomposition():
    model_output = {
        "status": "COMPLETED",
        "decomposition": [
            {
                "title": "Create User Model",
                "agent": "DATABASE_ENGINEER",
                "description": "Create a SQLAlchemy model for the User table with email and password hash."
            }
        ]
    }
    tasks, telemetry = parse_and_validate_tasks(model_output)
    assert len(tasks) == 1
    assert tasks[0].title == "Create User Model"
    assert tasks[0].agent == "DATABASE_ENGINEER"

def test_valid_artifacts_task_decomposition():
    model_output = {
        "status": "COMPLETED",
        "artifacts": {
            "task_decomposition": [
                {
                    "title": "Create User Model",
                    "agent": "DATABASE_ENGINEER",
                    "description": "Create a SQLAlchemy model for the User table with email and password hash."
                }
            ]
        }
    }
    tasks, telemetry = parse_and_validate_tasks(model_output)
    assert len(tasks) == 1
    assert tasks[0].title == "Create User Model"

def test_valid_artifacts_tasks():
    model_output = {
        "status": "COMPLETED",
        "artifacts": {
            "tasks": [
                {
                    "title": "Create User Model",
                    "agent": "DATABASE_ENGINEER",
                    "description": "Create a SQLAlchemy model for the User table with email and password hash."
                }
            ]
        }
    }
    tasks, telemetry = parse_and_validate_tasks(model_output)
    assert len(tasks) == 1

def test_null_description():
    model_output = {
        "decomposition": [
            {
                "title": "Create User Model",
                "agent": "DATABASE_ENGINEER",
                "description": None
            }
        ]
    }
    with pytest.raises(ValueError, match="failed schema validation"):
        parse_and_validate_tasks(model_output)

def test_missing_fields():
    model_output = {
        "decomposition": [
            {
                "title": "Create User Model"
            }
        ]
    }
    with pytest.raises(ValueError, match="failed schema validation"):
        parse_and_validate_tasks(model_output)

def test_not_a_list():
    model_output = {
        "decomposition": "This is a single string task"
    }
    with pytest.raises(ValueError, match="Tasks field is not a list"):
        parse_and_validate_tasks(model_output)

def test_empty_list():
    model_output = {
        "decomposition": []
    }
    with pytest.raises(ValueError, match="TASK_DECOMPOSITION_MISSING"):
        parse_and_validate_tasks(model_output)

def test_missing_decomposition():
    model_output = {
        "status": "SUCCESS"
    }
    with pytest.raises(ValueError, match="TASK_DECOMPOSITION_MISSING"):
        parse_and_validate_tasks(model_output)

def test_wrong_type():
    model_output = {
        "decomposition": [
            "Just a string task"
        ]
    }
    with pytest.raises(ValueError, match="is not an object"):
        parse_and_validate_tasks(model_output)
