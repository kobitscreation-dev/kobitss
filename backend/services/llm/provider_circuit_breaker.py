import time
import enum
from typing import Dict, Any, Optional

class CircuitState(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class ProviderCircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        
        self.state = CircuitState.HEALTHY
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.retry_after = 0.0

    def record_success(self):
        self.state = CircuitState.HEALTHY
        self.failure_count = 0
        self.retry_after = 0.0

    def record_failure(self, retry_after: Optional[float] = None):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if retry_after is not None:
            self.retry_after = self.last_failure_time + retry_after
        else:
            self.retry_after = self.last_failure_time + self.cooldown_seconds

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
        else:
            self.state = CircuitState.DEGRADED

    def can_execute(self) -> bool:
        if self.state == CircuitState.HEALTHY or self.state == CircuitState.DEGRADED:
            return True
        
        if self.state == CircuitState.OPEN:
            now = time.time()
            if now >= self.retry_after:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
            
        if self.state == CircuitState.HALF_OPEN:
            # allow one trial execution
            return True
            
        return True

# Registry for circuit breakers per provider
_circuit_breakers: Dict[str, ProviderCircuitBreaker] = {}

def get_circuit_breaker(provider_name: str) -> ProviderCircuitBreaker:
    if provider_name not in _circuit_breakers:
        _circuit_breakers[provider_name] = ProviderCircuitBreaker()
    return _circuit_breakers[provider_name]
