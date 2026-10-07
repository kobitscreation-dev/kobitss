from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured_output(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_turns: int = 10,
        tool_executor=None
    ) -> Dict[str, Any]:
        """
        Generates structured output. Can support multi-turn tool calling internally.
        tool_executor is a callback/interface that executes a tool call and returns the result.
        Returns the final structured output dictionary as well as usage metadata (tokens).
        """
        pass
