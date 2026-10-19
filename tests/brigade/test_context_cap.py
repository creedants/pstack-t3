from .support import (
    setUpModule,
    BrigadeTest,
    SCRIPT,
    Path,
    call,
    json,
    os,
    subprocess,
)
from .context_cap.test_open import CapOpenMixin
from .context_cap.test_set import CapSetMixin
from .context_cap.test_tracked_settings import TrackedSettingsMixin
from .context_cap.test_edits_and_status import EditsStatusMixin
from .context_cap.test_replace import CapReplaceMixin


class ContextCapTest(BrigadeTest, CapOpenMixin, CapSetMixin, TrackedSettingsMixin, EditsStatusMixin, CapReplaceMixin):
    def settings(self):
        return Path(self.project) / ".claude" / "settings.local.json"

    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def write_settings(self, text):
        """Write this text into the project's settings file, making the directory that holds it."""
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        self.settings().write_text(text)

    def drift(self, held, cap):
        """The reopen line for a settings file that holds held while the store's cap is cap."""
        return f"{self.settings()} holds autoCompactWindow {held}, not {cap}; run brigade.py set --context-cap {cap}"

    def missing_drift(self, cap):
        """The reopen line for a settings file that holds no integer cap while the store's cap is cap."""
        return f"{self.settings()} holds no integer autoCompactWindow; run brigade.py set --context-cap {cap}"

    def open(self, *extra, ok=True, code=None):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf", *extra, ok=ok, code=code)

    def edits_store_with_settings(self, settings):
        """The edits fixture with .claude/settings.local.json in every commit and a coordinator that writes no file."""
        self.git_project()
        reviewed = self.commit_files({"src/greeting.txt": self.SOURCE, "out/greeting.txt": self.SOURCE.upper(),
                                      "tools/build.py": self.BUILD, "tools/check.py": self.CHECK,
                                      ".claude/settings.local.json": settings})
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--coordinator-provider", "other")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("set", "--build-command", self.BUILD_COMMAND, "--check-command", self.CHECK_COMMAND)
        return reviewed

    def open_edits_dish(self):
        """D1 fired and in progress on a coordinator that writes no settings file, with the build and check commands set."""
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--coordinator-provider", "other")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("set", "--build-command", self.BUILD_COMMAND, "--check-command", self.CHECK_COMMAND)

    def head_settings_entry(self, kind, text, mode=None):
        """The head commit holding the FIXED greeting and one settings entry, and its sha.

        kind is "link" for a symbolic link whose target text is text, or "file" for a regular file.
        """
        link = self.settings()
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        if kind == "link":
            os.symlink(text, link)
        else:
            link.write_text(text)
            if mode is not None:
                link.chmod(mode)
        (self.project / "src").mkdir(parents=True, exist_ok=True)
        (self.project / "out").mkdir(parents=True, exist_ok=True)
        (self.project / "src" / "greeting.txt").write_text(self.FIXED)
        (self.project / "out" / "greeting.txt").write_text(self.FIXED.upper())
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "head")
        return run("git", "rev-parse", "HEAD").stdout.strip()

    def thread_landing(self):
        """A local landing contract and a store that records the thread t1."""
        self.init_landing()
        self.landing_env()
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf")
        self.brigade("set", "--thread", "t1")

    def store_a_cap_that_is_not_a_whole_number(self):
        """Write restaurant.json holding contextCap "400000" and return its bytes."""
        meta = self.meta()
        meta["contextCap"] = "400000"
        (self.at / "restaurant.json").write_text(json.dumps(meta))
        return (self.at / "restaurant.json").read_bytes()

