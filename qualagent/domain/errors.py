"""Domain exceptions shared by all layers.

Interface layers (CLI, REST, MCP) map these to the shared error codes defined
in 03_API_SPEC section 1.
"""

from __future__ import annotations


class QualAgentError(Exception):
    """Base class for all QualAgent domain errors."""

    error_code = "INTERNAL_ERROR"


class ProjectNotFound(QualAgentError):
    """Raised when a project id does not exist."""

    error_code = "PROJECT_NOT_FOUND"


class DocumentNotFound(QualAgentError):
    """Raised when a document id does not exist."""

    error_code = "DOCUMENT_NOT_FOUND"


class SegmentNotFound(QualAgentError):
    """Raised when a segment id does not exist."""

    error_code = "SEGMENT_NOT_FOUND"


class CodebookLocked(QualAgentError):
    """Raised when modifying a locked codebook."""

    error_code = "CODEBOOK_LOCKED"


class InvalidPack(QualAgentError):
    """Raised when a methodology pack fails validation."""

    error_code = "INVALID_PACK"


class LLMValidationFailed(QualAgentError):
    """Raised when structured LLM output fails validation after retries."""

    error_code = "LLM_VALIDATION_FAILED"


class RemoteProviderNotAccepted(QualAgentError):
    """Raised when a remote provider is used without explicit user consent (TAD D8)."""

    error_code = "REMOTE_PROVIDER_NOT_ACCEPTED"


class Conflict(QualAgentError):
    """Raised on uniqueness or state conflicts (e.g. duplicate project name)."""

    error_code = "CONFLICT"


class ValidationError(QualAgentError):
    """Raised on invalid user input or configuration."""

    error_code = "VALIDATION_ERROR"
