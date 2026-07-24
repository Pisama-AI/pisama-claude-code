"""Enhanced trace types with skill differentiation."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TraceType(Enum):
    """Types of traces captured from Claude Code."""

    TOOL = "tool"  # Regular tool calls (Bash, Read, Edit, etc.)
    SKILL = "skill"  # Skill activations (SKILL.md reads)
    TASK = "task"  # Task/subagent invocations
    MCP = "mcp"  # MCP server calls
    HOOK = "hook"  # Hook-triggered operations


class SkillSource(Enum):
    """Where the skill was loaded from."""

    PROJECT = "project"  # .claude/skills/ in repo
    PERSONAL = "personal"  # ~/.claude/skills/
    PLUGIN = "plugin"  # Installed plugin
    ENTERPRISE = "enterprise"


@dataclass
class EnhancedTrace:
    """Enhanced trace with skill differentiation."""

    trace_id: str
    session_id: str
    timestamp: datetime
    trace_type: TraceType

    # Tool info
    tool_name: str
    tool_input: Dict[str, Any]
    tool_output: Optional[Any] = None

    # Skill info (if trace_type == SKILL)
    skill_name: Optional[str] = None
    skill_source: Optional[SkillSource] = None
    skill_session_id: Optional[str] = None  # Groups skill-related calls

    # Context
    working_dir: str = ""
    hook_type: str = ""  # PreToolUse / PostToolUse

    # Detection results
    detections: List[Dict[str, Any]] = field(default_factory=list)

    metadata: Dict[str, Any] = field(default_factory=dict)


def classify_trace(raw_trace: Dict[str, Any]) -> EnhancedTrace:
    """Classify a raw trace into an enhanced trace with proper type."""
    tool_name = str(raw_trace.get("tool_name") or "unknown")
    tool_input = raw_trace.get("tool_input", {})
    raw_metadata = raw_trace.get("metadata")
    metadata: Dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}

    # Default classification
    trace_type = TraceType.TOOL
    skill_name = None
    skill_source = None

    # Detect skill activation via SKILL.md reads
    if tool_name == "Read":
        file_path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
        normalized_path = str(file_path).replace("\\", "/")
        if "SKILL.md" in normalized_path or "/skills/" in normalized_path:
            trace_type = TraceType.SKILL
            # Extract skill name from path
            match = re.search(r"(?:^|/)skills/([^/]+)/", normalized_path)
            skill_name = match.group(1) if match else "unknown"
            # Determine source
            working_dir = str(raw_trace.get("working_dir") or "").replace("\\", "/").rstrip("/")
            project_skills = f"{working_dir}/.claude/skills/" if working_dir else ".claude/skills/"
            absolute_path = normalized_path.startswith("/") or bool(
                re.match(r"^[A-Za-z]:/", normalized_path)
            )
            if not absolute_path or normalized_path.startswith(project_skills):
                skill_source = SkillSource.PROJECT
            elif "/.claude/skills/" in normalized_path:
                skill_source = SkillSource.PERSONAL
            else:
                skill_source = SkillSource.PLUGIN

    # Detect explicit Skill tool invocation
    elif tool_name == "Skill":
        trace_type = TraceType.SKILL
        skill_name = (
            tool_input.get("skill", "unknown") if isinstance(tool_input, dict) else "unknown"
        )

    # Detect Task/subagent invocation
    elif tool_name == "Task":
        trace_type = TraceType.TASK

    # Detect MCP calls
    elif tool_name.startswith("mcp__"):
        trace_type = TraceType.MCP

    return EnhancedTrace(
        trace_id=raw_trace.get("trace_id", ""),
        session_id=raw_trace.get("session_id", "unknown"),
        timestamp=_parse_trace_timestamp(raw_trace.get("timestamp")),
        trace_type=trace_type,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=raw_trace.get("tool_output"),
        skill_name=skill_name,
        skill_source=skill_source,
        working_dir=raw_trace.get("working_dir", ""),
        hook_type=raw_trace.get("hook_type", ""),
        metadata=metadata,
    )


def _parse_trace_timestamp(value: Any) -> datetime:
    """Parse ISO timestamps across every supported Python version."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def analyze_skill_usage(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze skill usage patterns from traces."""
    skills: Dict[str, Dict[str, Any]] = {}
    tool_calls = 0
    mcp_calls = 0
    task_calls = 0

    for raw_trace in traces:
        enhanced = classify_trace(raw_trace)

        if enhanced.trace_type == TraceType.SKILL:
            name = enhanced.skill_name or "unknown"
            if name not in skills:
                skills[name] = {
                    "count": 0,
                    "source": enhanced.skill_source.value if enhanced.skill_source else None,
                    "first_seen": enhanced.timestamp.isoformat(),
                    "last_seen": enhanced.timestamp.isoformat(),
                }
            skills[name]["count"] += 1
            skills[name]["last_seen"] = enhanced.timestamp.isoformat()
        elif enhanced.trace_type == TraceType.TOOL:
            tool_calls += 1
        elif enhanced.trace_type == TraceType.MCP:
            mcp_calls += 1
        elif enhanced.trace_type == TraceType.TASK:
            task_calls += 1

    return {
        "skills": skills,
        "tool_calls": tool_calls,
        "mcp_calls": mcp_calls,
        "task_calls": task_calls,
        "total_traces": len(traces),
    }
