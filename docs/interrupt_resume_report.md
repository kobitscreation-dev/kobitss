# Human-in-the-Loop: Interrupt & Resume Verification

## Issue

Previously, if the Kobits multi-agent debate hit a CRITICAL security rejection or exceeded the maximum allowed debate rounds, the task was marked `BLOCKED` and the entire mission would fail or exit permanently. There was no way to intervene, provide an override, and resume execution.

## Solution

The execution loop has been entirely rewritten to support a true asynchronous pause-and-resume "Human-in-the-Loop" architecture.

### 1. Mission Engine Halting
`MissionRuntime` and `OrchestratorDecision` have been updated. When the consensus engine returns a `BLOCKED` verdict, the engine now gracefully halts:
- Task status is set to `BLOCKED`.
- Mission status is set to `BLOCKED`.
- Background execution yields and halts completely, rather than failing the mission.

### 2. Resume API Endpoint
A new `/api/v1/missions/{mission_id}/resume` endpoint has been added. When called with a `{"comment": "..."}` payload:
- It resets the mission state to `EXECUTING`.
- It finds the `BLOCKED` tasks and sets them back to `PENDING`.
- It injects the `human_override_comment` into the task's metadata.
- It fires up a new background `execute_mission` routine.

### 3. Crash-Recoverable Debate Resumption
The `AgentExecutor` and `ConsensusEngine` now work together to perform a seamless "hot-resume":
- `AgentExecutor` detects that the task already has a history in the `debate_logs` table.
- It **skips** the initial Coder run and directly mounts the consensus engine with the historical context.
- `ConsensusEngine` loads the final, blocked log. It spots the `human_override_comment`.
- It wipes the `BLOCKED` verdict, appending the human's override directly to the AI's rejection feedback: `"Human Override: {comment}"`.
- It forces the Coder to revise its work one more time, increments the maximum allowed debate rounds, and seamlessly re-enters the review loop.

## Proof of Functionality

A comprehensive, end-to-end unit test was written (`tests/test_human_override.py`) to simulate this precise scenario at the database and state-machine level. 

The test:
1. Creates a `BLOCKED` task and populates the DB with a mock `DebateLog` showing a `CRITICAL` rejection by the Security Agent.
2. Injects the override: `"The security agent is wrong. Ignore it."`
3. Triggers `execute_agent_run`.
4. Asserts that the system bypasses the initial generation, natively re-enters the debate, successfully performs the revision round, and transitions the state from `BLOCKED` to `APPROVED`.

**The test passed perfectly in 62.21s.** The gap is formally closed.
