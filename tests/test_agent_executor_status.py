import pytest
from unittest.mock import patch, AsyncMock
from backend.services.agent_executor import AgentExecutor
from backend.models.agent import AgentRun, AgentRunStatus, AgentType, ResultClassification

@pytest.mark.asyncio
@pytest.mark.parametrize("mock_result, expected_classification, expected_status", [
    # Explicit successes
    ({"status": "SUCCESS"}, ResultClassification.COMPLETED, AgentRunStatus.COMPLETED),
    ({"status": "COMPLETED"}, ResultClassification.COMPLETED, AgentRunStatus.COMPLETED),
    ({"status": "COMPLETE"}, ResultClassification.COMPLETED, AgentRunStatus.COMPLETED),
    
    # Explicit failure
    ({"status": "FAILED"}, ResultClassification.FAILED, AgentRunStatus.FAILED),
    
    # Unknown status
    ({"status": "UNKNOWN_STR"}, ResultClassification.INDETERMINATE, AgentRunStatus.FAILED),
    
    # Missing status + Complete evidence (at least 2 evidence keys + _usage)
    (
        {"summary": "did work", "artifacts": {"x": 1}, "_usage": {}},
        ResultClassification.COMPLETED_WITH_INFERRED_STATUS,
        AgentRunStatus.COMPLETED
    ),
    
    # Missing status + Incomplete evidence (only 1 evidence key)
    (
        {"summary": "did work", "_usage": {}},
        ResultClassification.INDETERMINATE,
        AgentRunStatus.FAILED
    ),
    
    # Missing status + Complete evidence BUT missing _usage
    (
        {"summary": "did work", "artifacts": {"x": 1}},
        ResultClassification.INDETERMINATE,
        AgentRunStatus.FAILED
    ),
    
    # Malformed / Invalid JSON structure
    ("not a dict", ResultClassification.PARSE_ERROR, AgentRunStatus.FAILED),
])
async def test_agent_executor_status_handling(mock_result, expected_classification, expected_status):
    db_mock = AsyncMock()
    mock_result_obj = AsyncMock()
    mock_result_obj.scalars.return_value.all.return_value = []
    db_mock.execute.return_value = mock_result_obj
    
    run = AgentRun(id="test-run", status=AgentRunStatus.RUNNING)
    
    mock_provider = AsyncMock()
    # The provider just returns the dict/string it's given
    mock_provider.generate_structured_output.return_value = mock_result
    
    with patch("backend.services.agent_executor.get_llm_provider", return_value=mock_provider):
        executor = AgentExecutor()
        result = await executor.execute_run(run, AgentType.CODE_REVIEWER, {}, db=db_mock)
        
        # Verify run status is correctly set
        assert run.status == expected_status
        
        # Verify classification telemetry is attached if it was a dict
        if isinstance(result, dict) and "_classification" in result:
            assert result["_classification"]["model_result_status"] == expected_classification.value
            assert result["_classification"]["runtime_status"] == expected_status.value

