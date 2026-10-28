"""Built-in rule registration and discovery.

Rule ID allocation:

===========  ===================================================  =========
ID           Meaning                                              Source
===========  ===================================================  =========
MCP001       missing tool description                             contract
MCP002       excessive per-tool tokens                            tokens
MCP003       untyped or ``Any`` parameter                         schema
MCP004       unbounded collection / limit                         schema
MCP005       dangerous shell execution                            security
MCP006       unvalidated SSRF sink                                security
MCP007       unconfined filesystem access                         security
MCP008       ambiguous or duplicate tool name                     contract
MCP009       hardcoded secret in tool body                        security
MCP010       missing network timeout                              security
MCP011       SQL string interpolation                             security
MCP012       live protocol / transport violation                  live audit
MCP013       unsafe deserialization                               security
MCP014       server-wide token budget exceeded                    tokens
MCP015       undocumented tool parameter                          schema
===========  ===================================================  =========

``MCP012`` is emitted by :mod:`mcp_doctor.live` during live introspection, which is why
it has no module in this package. Third-party rule packs must use their own prefix
(see :mod:`mcp_doctor.rules.plugin`), so the ``MCP`` namespace stays trustworthy.
"""

from mcp_doctor.rules import (  # noqa: F401
    contract_rules,
    schema_rules,
    security_rules,
    token_rules,
)
from mcp_doctor.rules.base import (
    RuleDefinition,
    get_all_rules,
    get_rule_catalog,
    register_definition,
    register_rule,
    restore_registry,
    snapshot_registry,
)

__all__ = [
    "RuleDefinition",
    "get_all_rules",
    "get_rule_catalog",
    "register_definition",
    "register_rule",
    "restore_registry",
    "snapshot_registry",
]
