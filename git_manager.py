import os
import subprocess
import logging
from pathlib import Path
import config
from security import mask_secrets

logger = logging.getLogger("git_manager")

class GitManager:
    @staticmethod
    def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
        """Runs a git command inside cwd safely."""
        env = os.environ.copy()
        # If GITHUB_TOKEN is available, configure git credentials helper or url
        if config.GITHUB_TOKEN:
            env["GIT_ASKPASS"] = "echo"
            env["GIT_TERMINAL_PROMPT"] = "0"
            
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=45,
                env=env
            )
            return res.returncode, res.stdout.strip()
        except Exception as e:
            return -1, str(e)

    @classmethod
    def ensure_repo(cls, project_dir: Path) -> bool:
        """Initializes a git repository if not already present."""
        if not (project_dir / ".git").exists():
            code, _ = cls.run_git(["init"], project_dir)
            if code != 0:
                return False
            # Create a default .gitignore inside project
            gitignore = project_dir / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(".env\n__pycache__/\n*.pyc\n*.log\nused_proxies.txt\n")
            cls.run_git(["add", ".gitignore"], project_dir)
            cls.run_git(["commit", "-m", "chore: initial commit with gitignore"], project_dir)
        return True

    @classmethod
    def create_checkpoint(cls, project_dir: Path, task_title: str) -> tuple[bool, str]:
        """Creates a snapshot git commit before task begins for /undo capability."""
        cls.ensure_repo(project_dir)
        # Stage everything
        cls.run_git(["add", "-A"], project_dir)
        
        # Check if there are changes to commit
        code, status = cls.run_git(["status", "--porcelain"], project_dir)
        if not status:
            return True, "No changes to checkpoint"

        msg = f"checkpoint before task: {task_title[:40]}"
        code, out = cls.run_git(["commit", "-m", msg], project_dir)
        if code == 0:
            logger.info(f"Checkpoint created: {msg}")
            return True, msg
        return False, out

    @classmethod
    def undo_last_checkpoint(cls, project_dir: Path) -> tuple[bool, str]:
        """Reverts workspace to previous checkpoint."""
        if not (project_dir / ".git").exists():
            return False, "Project mein koi git repository nahi mili."

        # Hard reset to previous commit
        code, out = cls.run_git(["reset", "--hard", "HEAD~1"], project_dir)
        if code == 0:
            cls.run_git(["clean", "-fd"], project_dir)
            return True, "Aakhri checkpoint par safalta se wapas reset ho gaya."
        return False, f"Undo fail hua: {out}"

    @classmethod
    def auto_commit_and_push(cls, project_dir: Path, summary_msg: str) -> tuple[bool, str]:
        """Commits changes after task completion and pushes to GitHub if remote configured."""
        cls.ensure_repo(project_dir)
        
        # Check if secrets would be accidentally committed
        is_safe, secrets_found = cls.check_staged_secrets(project_dir)
        if not is_safe:
            return False, f"Commit blocked! Sensitive file/token detected: {', '.join(secrets_found)}"

        cls.run_git(["add", "-A"], project_dir)
        code, status = cls.run_git(["status", "--porcelain"], project_dir)
        if not status:
            return True, "No changes to commit"

        commit_msg = summary_msg if summary_msg else "fix: automated task updates"
        code, out = cls.run_git(["commit", "-m", commit_msg], project_dir)
        if code != 0:
            return False, f"Commit fail hua: {out}"

        # Get commit hash
        _, commit_hash = cls.run_git(["rev-parse", "--short", "HEAD"], project_dir)

        # Check if origin remote exists
        _, remotes = cls.run_git(["remote"], project_dir)
        if "origin" in remotes.split():
            code, push_out = cls.run_git(["push", "origin", "main"], project_dir)
            if code == 0:
                return True, f"Pushed to GitHub (commit `{commit_hash}`)"
            return True, f"Committed locally (`{commit_hash}`). Push failed: {push_out}"

        return True, f"Committed locally (`{commit_hash}`)"

    @classmethod
    def check_staged_secrets(cls, project_dir: Path) -> tuple[bool, list[str]]:
        """Scans workspace to prevent pushing .env or private key files."""
        violations = []
        for p in project_dir.rglob("*"):
            if p.is_file() and not any(part.startswith(".git") for part in p.parts):
                if p.name == ".env" or p.name.endswith(".key") or p.name.endswith(".pem"):
                    # Check if git is tracking this
                    code, tracked = cls.run_git(["ls-files", str(p.relative_to(project_dir))], project_dir)
                    if tracked:
                        violations.append(p.name)
        if violations:
            return False, violations
        return True, []
