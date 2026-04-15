"""
Skill Guard — security scanner for auto-generated skills.

All automatically generated Skills must pass safety checks before activation.
Reference: Hermes tools/skills_guard.py

Scan categories:
- exfiltration: curl/wget + secrets, external data sends
- injection: prompt injection patterns
- destructive: rm -rf, DROP TABLE, git push --force
- persistence: crontab, systemd, launchd
- obfuscation: base64 encode of commands, eval()

Trust levels:
- bundled: safe=allow, caution=allow, dangerous=allow
- agent-created: safe=allow, caution=allow, dangerous=ASK_USER
- hub-installed: safe=allow, caution=ASK_USER, dangerous=DENY
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class TrustLevel(str, Enum):
    BUNDLED = "bundled"          # Shipped with RRAgent
    AGENT_CREATED = "agent"      # Created by Background Review / Evolution
    HUB_INSTALLED = "hub"        # From ClawHub community


class ScanSeverity(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"


class ScanDecision(str, Enum):
    ALLOW = "allow"
    ASK_USER = "ask_user"
    DENY = "deny"


@dataclass
class ScanFinding:
    """A single security finding."""

    category: str
    severity: ScanSeverity
    pattern_matched: str
    line_number: int = 0
    context: str = ""
    description: str = ""


@dataclass
class ScanResult:
    """Result of scanning a skill."""

    skill_name: str
    trust_level: TrustLevel
    findings: list[ScanFinding] = field(default_factory=list)
    decision: ScanDecision = ScanDecision.ALLOW
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.decision in (ScanDecision.ALLOW, ScanDecision.ASK_USER)

    @property
    def max_severity(self) -> ScanSeverity:
        if not self.findings:
            return ScanSeverity.SAFE
        severities = [f.severity for f in self.findings]
        if ScanSeverity.DANGEROUS in severities:
            return ScanSeverity.DANGEROUS
        if ScanSeverity.CAUTION in severities:
            return ScanSeverity.CAUTION
        return ScanSeverity.SAFE


# Decision matrix: trust_level x severity -> decision
TRUST_MATRIX: dict[TrustLevel, dict[ScanSeverity, ScanDecision]] = {
    TrustLevel.BUNDLED: {
        ScanSeverity.SAFE: ScanDecision.ALLOW,
        ScanSeverity.CAUTION: ScanDecision.ALLOW,
        ScanSeverity.DANGEROUS: ScanDecision.ALLOW,
    },
    TrustLevel.AGENT_CREATED: {
        ScanSeverity.SAFE: ScanDecision.ALLOW,
        ScanSeverity.CAUTION: ScanDecision.ALLOW,
        ScanSeverity.DANGEROUS: ScanDecision.ASK_USER,
    },
    TrustLevel.HUB_INSTALLED: {
        ScanSeverity.SAFE: ScanDecision.ALLOW,
        ScanSeverity.CAUTION: ScanDecision.ASK_USER,
        ScanSeverity.DANGEROUS: ScanDecision.DENY,
    },
}


# Pattern definitions for each category
SCAN_PATTERNS: list[dict] = [
    # Exfiltration
    {
        "category": "exfiltration",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'curl\s+.*(-d|--data|--upload-file)\s+',
        "description": "Outbound data transfer via curl",
    },
    {
        "category": "exfiltration",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'wget\s+--post-(data|file)',
        "description": "Outbound data transfer via wget",
    },
    {
        "category": "exfiltration",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'(API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)\s*[=:]',
        "description": "Hardcoded secret reference",
    },
    {
        "category": "exfiltration",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'requests\.(post|put|patch)\s*\(',
        "description": "HTTP POST/PUT via requests library",
    },
    {
        "category": "exfiltration",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'(smtp|mail|email)\.',
        "description": "Email sending capability",
    },

    # Injection
    {
        "category": "injection",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'(ignore\s+previous|forget\s+all|system\s*:\s*you\s+are)',
        "description": "Prompt injection pattern",
    },
    {
        "category": "injection",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'(eval|exec|compile)\s*\(',
        "description": "Dynamic code execution",
    },
    {
        "category": "injection",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'__import__\s*\(',
        "description": "Dynamic import",
    },

    # Destructive
    {
        "category": "destructive",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'\brm\s+(-rf|-fr)\b',
        "description": "Recursive force delete",
    },
    {
        "category": "destructive",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'\b(DROP|TRUNCATE)\s+(TABLE|DATABASE)\b',
        "description": "Database destruction",
    },
    {
        "category": "destructive",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'\bgit\s+push\s+--force\b',
        "description": "Git force push",
    },
    {
        "category": "destructive",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'\bgit\s+reset\s+--hard\b',
        "description": "Git hard reset",
    },
    {
        "category": "destructive",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'\b(kill|pkill|killall)\s+',
        "description": "Process termination",
    },
    {
        "category": "destructive",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'\b(mkfs|dd\s+if=|fdisk)\b',
        "description": "Disk/filesystem operations",
    },

    # Persistence
    {
        "category": "persistence",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'\b(crontab|at\s+-f)\b',
        "description": "Scheduled task creation",
    },
    {
        "category": "persistence",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'(systemctl|launchctl)\s+(enable|load|start)',
        "description": "Service installation",
    },
    {
        "category": "persistence",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'\.(bashrc|zshrc|profile)\b',
        "description": "Shell config modification",
    },

    # Obfuscation
    {
        "category": "obfuscation",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'base64\s+(-d|--decode)',
        "description": "Base64 decode (possible hidden command)",
    },
    {
        "category": "obfuscation",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r'echo\s+[\w+/=]+\s*\|\s*base64',
        "description": "Base64 encode pipeline",
    },
    {
        "category": "obfuscation",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'\\x[0-9a-fA-F]{2}',
        "description": "Hex-encoded content",
    },

    # Fork bomb / resource exhaustion
    {
        "category": "destructive",
        "severity": ScanSeverity.DANGEROUS,
        "pattern": r':\(\)\s*\{\s*:\|:\s*&\s*\}',
        "description": "Fork bomb",
    },
    {
        "category": "destructive",
        "severity": ScanSeverity.CAUTION,
        "pattern": r'while\s+true\s*;?\s*do',
        "description": "Infinite loop",
    },
]


class SkillGuard:
    """
    Security scanner for Skills.

    Scans skill content (YAML frontmatter + Markdown body) for:
    - Data exfiltration patterns
    - Prompt injection
    - Destructive operations
    - Persistence mechanisms
    - Code obfuscation

    Decision is based on trust level + highest severity finding.
    """

    def __init__(self, extra_patterns: list[dict] | None = None):
        self.patterns = SCAN_PATTERNS.copy()
        if extra_patterns:
            self.patterns.extend(extra_patterns)

        # Compile regex patterns
        self._compiled = [
            {
                **p,
                "_regex": re.compile(p["pattern"], re.IGNORECASE),
            }
            for p in self.patterns
        ]

    def scan(
        self,
        skill_name: str,
        content: str,
        trust_level: TrustLevel = TrustLevel.AGENT_CREATED,
    ) -> ScanResult:
        """
        Scan a skill's content for security issues.

        Args:
            skill_name: Name of the skill
            content: Full skill content (YAML + Markdown)
            trust_level: Trust level of the skill source

        Returns:
            ScanResult with findings and decision
        """
        result = ScanResult(skill_name=skill_name, trust_level=trust_level)

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            for pattern_def in self._compiled:
                regex = pattern_def["_regex"]
                if regex.search(line):
                    result.findings.append(ScanFinding(
                        category=pattern_def["category"],
                        severity=pattern_def["severity"],
                        pattern_matched=pattern_def["pattern"],
                        line_number=line_num,
                        context=line.strip()[:200],
                        description=pattern_def["description"],
                    ))

        # Determine decision based on trust matrix
        max_severity = result.max_severity
        result.decision = TRUST_MATRIX[trust_level][max_severity]

        if result.findings:
            categories = set(f.category for f in result.findings)
            result.reason = (
                f"Found {len(result.findings)} issue(s) in categories: "
                f"{', '.join(categories)}. "
                f"Max severity: {max_severity.value}. "
                f"Trust level: {trust_level.value}."
            )

        return result

    def scan_quick(self, content: str) -> bool:
        """Quick scan: returns True if content appears safe."""
        for pattern_def in self._compiled:
            if pattern_def["severity"] == ScanSeverity.DANGEROUS:
                regex = pattern_def["_regex"]
                if regex.search(content):
                    return False
        return True
