"""API error shape {code, message, hint}; the UI translates by code (web/src/i18n/*.json: error.<code>)."""

CODES = (
    "decode_failed", "no_audio", "too_long", "disk_full", "needs_stream", "unknown_module",
    "module_not_installed", "module_unavailable", "project_not_found", "invalid_state", "file_not_found",
    "out_of_memory", "gpu_oom", "stage_check_failed", "integrity_failed", "worker_lost", "internal_error",
    "cancelled", "nvidia_missing", "install_disabled", "not_enough_space", "install_failed", "uninstall_failed",
    "start_failed", "selftest_failed", "busy", "bad_request", "interrupted", "packages_missing",
    "gpu_worker_offline", "forbidden_origin", "not_local",
    "not_allowed", "folder_not_found", "folder_read_only", "folder_not_empty", "set_by_env", "download_failed",
)


class ApiError(Exception):
    def __init__(self, status, code, message, hint=""):
        super().__init__(message)
        assert code in CODES, code
        self.status, self.code, self.message, self.hint = status, code, message, hint

    def body(self):
        return {"code": self.code, "message": self.message, "hint": self.hint}


def from_afix(e, status=422):
    code = e.code if e.code in CODES else "internal_error"
    return ApiError(status, code, e.message, e.hint)
