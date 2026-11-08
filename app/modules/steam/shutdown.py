from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psutil

logger = logging.getLogger(__name__)


class GameCloseError(RuntimeError):
    def __init__(self, message: str, state: dict):
        super().__init__(message)
        self.state = state


class ProcessIdentityError(RuntimeError):
    def __init__(self, message: str, pid: int, executable: str):
        super().__init__(message)
        self.pid = pid
        self.executable = executable or "unknown"


@dataclass
class ShutdownSnapshot:
    state: str = "RUNNING"
    origin: str | None = None
    method: str | None = None
    pids: list[int] = field(default_factory=list)
    executable: str | None = None
    requested_at: str | None = None
    elapsed_seconds: float = 0.0
    result: str | None = None
    error: str | None = None
    attempt: int = 0

    def as_dict(self) -> dict:
        return {
            "state": self.state, "origin": self.origin, "method": self.method,
            "pids": list(self.pids), "executable": self.executable,
            "requested_at": self.requested_at,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "result": self.result, "error": self.error, "attempt": self.attempt,
        }


class GameShutdownService:
    """Owns all graceful and explicitly confirmed forced ACC process closure."""

    def __init__(self, integration, first_wait_seconds: int = 15,
                 additional_wait_seconds: int = 30, force_terminate_wait_seconds: int = 3,
                 poll_interval_seconds: float = 0.2):
        self.integration = integration
        self.first_wait_seconds = max(0, first_wait_seconds)
        self.additional_wait_seconds = max(0, additional_wait_seconds)
        self.force_terminate_wait_seconds = max(0, force_terminate_wait_seconds)
        self.poll_interval_seconds = max(0.01, poll_interval_seconds)
        self._operation_lock = threading.RLock()
        self._snapshot_lock = threading.Lock()
        self._snapshot = ShutdownSnapshot()
        self._fingerprint: tuple[tuple[int, str], ...] = ()
        self._attempt_started = time.monotonic()
        self._attempt_requested_at: str | None = None

    def status(self) -> dict:
        with self._snapshot_lock:
            return self._snapshot.as_dict()

    def close(self, origin: str, retry: bool = False) -> dict:
        with self._operation_lock:
            try:
                targets = self._validated_targets()
            except ProcessIdentityError as exc:
                current = self.status()
                logger.error("[ACC-SHUTDOWN] time=%s pid=%s executable=%s origin=%s method=identity_validation result=failed elapsed_seconds=0 error=%s",
                             datetime.now(timezone.utc).isoformat(), exc.pid, exc.executable, origin, str(exc))
                failed = self._set(state="CLOSE_FAILED", origin=origin, method="identity_validation",
                                   pids=current.get("pids", []), executable=current.get("executable"),
                                   result="identity_unverified", error=str(exc), elapsed=0, new_attempt=True)
                raise GameCloseError(str(exc), failed) from exc
            if not targets:
                self._set(state="CLOSED", origin=origin, method="process_check",
                          pids=[], executable=None, result="already_exited", error=None,
                          elapsed=0, new_attempt=False)
                return self.status()

            fingerprint = tuple(sorted((target.pid, self.integration._normalize_path(target.executable))
                                       for target in targets))
            current = self.status()
            same_targets = fingerprint == self._fingerprint
            if same_targets and current["state"] == "CLOSE_FAILED" and not retry:
                raise GameCloseError(current["error"] or "O ACC ainda está em execução.", current)
            if same_targets and current["state"] in {"CLOSE_REQUESTED", "WAITING_FOR_EXIT", "FORCE_CLOSING"}:
                return current

            self._fingerprint = fingerprint
            started = time.monotonic()
            self._set(state="CLOSE_REQUESTED", origin=origin, method="WM_CLOSE",
                      pids=[target.pid for target in targets], executable=targets[0].executable,
                      result=None, error=None, elapsed=0, new_attempt=True)
            self._log(targets, origin, "WM_CLOSE", "requested", 0)

            try:
                sent = self.integration._post_close_to_windows({target.pid for target in targets})
            except Exception as exc:
                sent = 0
                logger.exception("[ACC-SHUTDOWN] WM_CLOSE falhou para PID(s) %s", [t.pid for t in targets])
                error = f"Não foi possível solicitar fechamento normal: {exc}"
            else:
                error = None

            self._set(state="WAITING_FOR_EXIT", origin=origin, method="WM_CLOSE",
                      pids=[target.pid for target in targets], executable=targets[0].executable,
                      result="message_sent" if sent else "no_window_confirmed",
                      error=None, elapsed=time.monotonic() - started, new_attempt=False)

            if self._wait_targets_exit(targets, self.first_wait_seconds):
                return self._closed(targets, origin, started, "wm_close", "closed_within_first_wait")

            # A single WM_CLOSE request is allowed a 15 second initial window and
            # then up to 30 additional seconds. Never resend it in a loop.
            if self._wait_targets_exit(targets, self.additional_wait_seconds):
                return self._closed(targets, origin, started, "wm_close", "closed_within_additional_wait")

            elapsed = time.monotonic() - started
            detail = error or ("O ACC continua ativo após 15 s iniciais e mais 30 s de espera. "
                               "Nenhum encerramento forçado foi executado.")
            failed = self._set(state="CLOSE_FAILED", origin=origin, method="WM_CLOSE",
                               pids=[target.pid for target in targets], executable=targets[0].executable,
                               result="timeout", error=detail, elapsed=elapsed, new_attempt=False)
            self._log(targets, origin, "WM_CLOSE", "timeout", elapsed)
            raise GameCloseError(detail, failed)

    def force_close(self, origin: str, confirmed: bool) -> dict:
        if not confirmed:
            raise ValueError("Confirmação explícita é obrigatória para forçar o encerramento.")
        with self._operation_lock:
            try:
                targets = self._validated_targets()
            except ProcessIdentityError as exc:
                logger.error("[ACC-SHUTDOWN] time=%s pid=%s executable=%s origin=%s method=identity_validation result=failed elapsed_seconds=0 error=%s",
                             datetime.now(timezone.utc).isoformat(), exc.pid, exc.executable, origin, str(exc))
                failed = self._set(state="CLOSE_FAILED", origin=origin, method="identity_validation",
                                   pids=[], executable=None, result="identity_unverified",
                                   error=str(exc), elapsed=0, new_attempt=False)
                raise GameCloseError(str(exc), failed) from exc
            if not targets:
                return self._set(state="CLOSED", origin=origin, method="process_check",
                                 pids=[], executable=None, result="already_exited", error=None,
                                 elapsed=0, new_attempt=False)
            prior = self.status()
            if prior["state"] != "CLOSE_FAILED":
                raise RuntimeError("O fechamento normal ainda não falhou; encerramento forçado indisponível.")
            fingerprint = tuple(sorted((target.pid, self.integration._normalize_path(target.executable))
                                       for target in targets))
            if fingerprint != self._fingerprint:
                raise RuntimeError("O PID do ACC mudou desde a falha; faça uma nova tentativa normal antes da confirmação forçada.")

            started = time.monotonic()
            self._set(state="FORCE_CLOSING", origin=origin, method="terminate_then_kill_confirmed",
                      pids=[target.pid for target in targets], executable=targets[0].executable,
                      result="confirmed", error=None, elapsed=0, new_attempt=True)
            self._log(targets, origin, "terminate_then_kill_confirmed", "confirmed", 0)

            processes = []
            try:
                for target in targets:
                    process = self._process_if_same(target)
                    if process is not None:
                        processes.append(process)
            except ProcessIdentityError as exc:
                logger.error("[ACC-SHUTDOWN] time=%s pid=%s executable=%s origin=%s method=identity_validation result=failed elapsed_seconds=0 error=%s",
                             datetime.now(timezone.utc).isoformat(), exc.pid, exc.executable, origin, str(exc))
                failed = self._set(state="CLOSE_FAILED", origin=origin, method="identity_validation",
                                   pids=[target.pid for target in targets], executable=targets[0].executable,
                                   result="identity_unverified", error=str(exc), elapsed=0, new_attempt=False)
                raise GameCloseError(str(exc), failed) from exc
            for process in processes:
                try:
                    process.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    continue
            _, alive = psutil.wait_procs(processes, timeout=self.force_terminate_wait_seconds)
            for process in alive:
                try:
                    target = next(item for item in targets if item.pid == process.pid)
                    if self._process_if_same(target) is not None:
                        process.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ProcessIdentityError, StopIteration):
                    continue

            if self._wait_targets_exit(targets, self.force_terminate_wait_seconds):
                return self._closed(targets, origin, started, "force_confirmed", "closed")
            elapsed = time.monotonic() - started
            detail = "O processo do ACC continua ativo mesmo após a tentativa forçada confirmada."
            failed = self._set(state="CLOSE_FAILED", origin=origin, method="terminate_then_kill_confirmed",
                               pids=[target.pid for target in targets], executable=targets[0].executable,
                               result="process_still_alive", error=detail, elapsed=elapsed, new_attempt=False)
            self._log(targets, origin, "terminate_then_kill_confirmed", "failed", elapsed)
            raise GameCloseError(detail, failed)

    def _validated_targets(self):
        targets = []
        for target in self.integration.find_game_processes():
            if not target.executable:
                raise ProcessIdentityError(
                    f"Não foi possível validar o caminho do PID {target.pid}; nenhum sinal foi enviado.",
                    target.pid, target.executable)
            process = self._process_if_same(target)
            if process is not None:
                targets.append(target)
        return targets

    def _process_if_same(self, target):
        try:
            process = psutil.Process(target.pid)
            if (process.name().casefold() != self.integration.game.executable_name.casefold()
                    or self.integration._normalize_path(process.exe()) != self.integration._normalize_path(target.executable)):
                return None
            return process
        except psutil.NoSuchProcess:
            return None
        except (psutil.AccessDenied, OSError) as exc:
            # Do not claim the process exited or signal it when identity is unknown.
            raise ProcessIdentityError(f"Não foi possível revalidar o PID {target.pid}; o simulador permanece bloqueado.",
                                       target.pid, target.executable) from exc

    def _wait_targets_exit(self, targets, timeout: int) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            alive = []
            for target in targets:
                try:
                    if self._process_if_same(target) is not None:
                        alive.append(target)
                except ProcessIdentityError:
                    alive.append(target)
            if not alive:
                return True
            if time.monotonic() >= deadline:
                return False
            self._set(state="WAITING_FOR_EXIT", origin=self.status()["origin"],
                      method=self.status()["method"], pids=[t.pid for t in alive],
                      executable=alive[0].executable, result="waiting",
                      error=None, elapsed=time.monotonic() - self._attempt_started, new_attempt=False)
            time.sleep(min(self.poll_interval_seconds, max(0, deadline - time.monotonic())))

    def _closed(self, targets, origin: str, started: float, method: str, result: str) -> dict:
        elapsed = time.monotonic() - started
        self._log(targets, origin, method, result, elapsed)
        return self._set(state="CLOSED", origin=origin, method=method,
                         pids=[target.pid for target in targets],
                         executable=targets[0].executable if targets else None,
                         result=result, error=None, elapsed=elapsed, new_attempt=False)

    def _set(self, *, state, origin, method, pids, executable, result, error, elapsed, new_attempt):
        with self._snapshot_lock:
            attempt = self._snapshot.attempt + 1 if new_attempt else self._snapshot.attempt
            if new_attempt:
                self._attempt_started = time.monotonic()
                self._attempt_requested_at = datetime.now(timezone.utc).isoformat()
            self._snapshot = ShutdownSnapshot(state=state, origin=origin, method=method,
                pids=list(pids), executable=executable,
                requested_at=self._attempt_requested_at or datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=max(0, elapsed), result=result, error=error, attempt=attempt)
            return self._snapshot.as_dict()

    @staticmethod
    def _log(targets, origin: str, method: str, result: str, elapsed: float) -> None:
        for target in targets:
            logger.info("[ACC-SHUTDOWN] time=%s pid=%s executable=%s origin=%s method=%s result=%s elapsed_seconds=%.2f",
                        datetime.now(timezone.utc).isoformat(), target.pid, target.executable,
                        origin, method, result, elapsed)
