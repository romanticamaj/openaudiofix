"""Errors that carry a stable `code` (used by the API and translated by the UI)."""


class AfixError(Exception):
    code = "internal_error"

    def __init__(self, message, hint="", code=None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code:
            self.code = code


class InputError(AfixError):
    code = "decode_failed"


class StageCheckError(AfixError):
    code = "stage_check_failed"


class IntegrityError(AfixError):
    code = "integrity_failed"


class ModuleUnavailable(AfixError):
    code = "module_unavailable"


class GpuOutOfMemory(AfixError):
    code = "gpu_oom"


class Cancelled(AfixError):
    code = "cancelled"
