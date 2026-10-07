import pytest
import numpy as np
import time
from backend.services.embedding import EmbeddingUtils
from backend.services.memory_service import MemoryService
from backend.models.memory import CodeDocument
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_search_quality():
    # Deterministic fixture testing
    text_related_1 = "def parse_json_payload(request):"
    text_related_2 = "def handle_json_request(payload):"
    text_unrelated = "class DatabaseConnection:"
    
    vec_r1 = np.array(EmbeddingUtils.generate_embedding(text_related_1))
    vec_r2 = np.array(EmbeddingUtils.generate_embedding(text_related_2))
    vec_un = np.array(EmbeddingUtils.generate_embedding(text_unrelated))
    
    sim_r1_r2 = np.dot(vec_r1, vec_r2)
    sim_r1_un = np.dot(vec_r1, vec_un)
    
    # Verify related code ranks above unrelated code
    assert sim_r1_r2 > sim_r1_un
    
    # Test project isolation
    db_mock = AsyncMock()
    doc1 = CodeDocument(file_path="json_parser.py", content_chunk=text_related_1, embedding=vec_r1.tolist(), project_id="proj1")
    
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [doc1]
    db_mock.execute.return_value = mock_result
    
    results = await MemoryService.search_codebase(db_mock, "proj1", "json payload")
    assert len(results) == 1
    
    # Test empty/no-match searches fail gracefully
    # Assuming threshold is 0.9 for "json payload" vs "random nonsense" which will score very low
    results_empty = await MemoryService.search_codebase(db_mock, "proj1", "xyzzzzxyxyzzyzyxzyxzyx", threshold=0.99)
    assert len(results_empty) == 0

@pytest.mark.asyncio
async def test_linear_scan_performance():
    # Create 1000 documents
    docs_1k = []
    base_vec = np.zeros(384)
    base_vec[0] = 1.0
    for i in range(1000):
        docs_1k.append(CodeDocument(file_path=f"file_{i}.py", content_chunk="test", embedding=base_vec.tolist(), project_id="proj1"))
        
    db_mock = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = docs_1k
    db_mock.execute.return_value = mock_result
    
    start_time = time.time()
    await MemoryService.search_codebase(db_mock, "proj1", "test query")
    end_time = time.time()
    
    time_1k = end_time - start_time
    print(f"\\n1,000 documents linear scan time: {time_1k:.4f} seconds")
    
    # Create 10000 documents
    docs_10k = docs_1k * 10
    mock_result.scalars().all.return_value = docs_10k
    
    start_time = time.time()
    await MemoryService.search_codebase(db_mock, "proj1", "test query")
    end_time = time.time()
    
    time_10k = end_time - start_time
    print(f"10,000 documents linear scan time: {time_10k:.4f} seconds")
    
    # Assert it takes less than 1 second for 10k documents
    assert time_10k < 1.0

def test_determinism_and_dimensions():
    # Verify exact 384 dimensions and determinism
    text = "const a = 1;"
    vec1 = EmbeddingUtils.generate_embedding(text)
    vec2 = EmbeddingUtils.generate_embedding(text)
    
    assert len(vec1) == 384
    assert vec1 == vec2
    
    # Verify no NaNs and normalized
    np_vec = np.array(vec1)
    assert not np.isnan(np_vec).any()
    assert not np.isinf(np_vec).any()
    assert np.isclose(np.linalg.norm(np_vec), 1.0)
    
    # Empty text handles gracefully
    vec_empty = EmbeddingUtils.generate_embedding("")
    assert len(vec_empty) == 384
    assert np.all(np.array(vec_empty) == 0.0)
