"""Tests for cumbre_session.py. Run: python3 -m unittest discover -s scripts/discovery -p "test_*.py" """
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

import cumbre_session as kit

FIXTURES = Path(__file__).with_name("fixtures")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = kit.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class SessionKitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())  # outside the repo, like CUMBRE_CLIENTS_DIR
        self.addCleanup(shutil.rmtree, self.tmp)

    def new(self, *extra):
        return run("new", "--client", "Panadería Ñandú S.A.S.", "--date", "2026-11-10",
                   "--clients-dir", str(self.tmp), *extra)

    def session(self, sid="2026-11-10"):
        return self.tmp / "panaderia-nandu-s-a-s" / "sessions" / sid

    def test_slugify_handles_accents(self):
        self.assertEqual(kit.slugify("Panadería Ñandú S.A.S."), "panaderia-nandu-s-a-s")
        with self.assertRaises(kit.KitError):
            kit.slugify("¡¿?!")

    def test_new_creates_structure_and_copies_inputs(self):
        code, _ = self.new("--transcript", str(FIXTURES / "synthetic_transcript.md"),
                           "--notes", str(FIXTURES / "synthetic_notes.md"))
        self.assertEqual(code, 0)
        s = self.session()
        cfg = (s / "session.yaml").read_text(encoding="utf-8")
        self.assertIn('client_name: "Panadería Ñandú S.A.S."', cfg)
        self.assertIn("session_date: 2026-11-10", cfg)
        self.assertTrue((s / "inputs/transcript/synthetic_transcript.md").is_file())
        self.assertTrue((s / "outputs").is_dir())
        self.assertTrue((FIXTURES / "synthetic_transcript.md").is_file(), "source must be copied, not moved")
        self.assertEqual(len(kit.load_manifest(s)), 2)

    def test_same_date_never_overwrites(self):
        self.new()
        self.new()
        self.assertTrue(self.session("2026-11-10-2").is_dir())

    def test_missing_input_fails_before_creating_folder(self):
        code, msg = self.new("--transcript", str(self.tmp / "nope.txt"))
        self.assertEqual(code, 2)
        self.assertIn("not found", msg)
        self.assertFalse(self.session().exists())

    def test_rejects_bad_date(self):
        code, _ = run("new", "--client", "X", "--date", "10/11/2026", "--clients-dir", str(self.tmp))
        self.assertEqual(code, 2)

    def test_refuses_unignored_folder_inside_repo(self):
        target = kit.REPO_ROOT / "scripts" / "discovery" / "_not_ignored_test"
        self.addCleanup(shutil.rmtree, target, True)
        code, msg = run("new", "--client", "X", "--date", "2026-11-10", "--clients-dir", str(target))
        self.assertEqual(code, 2)
        self.assertIn("refusing", msg)
        self.assertFalse(target.exists())

    def test_check_detects_empty_inputs_and_modified_sources(self):
        self.new()
        errors, _, _ = kit.check_session(self.session())
        self.assertTrue(any("inputs/ is empty" in e for e in errors))

        self.new("--transcript", str(FIXTURES / "synthetic_transcript.md"))
        s = self.session("2026-11-10-2")
        self.assertEqual(kit.check_session(s)[0], [])
        with open(s / "inputs/transcript/synthetic_transcript.md", "a", encoding="utf-8") as fh:
            fh.write("edit")
        self.assertTrue(any("modified" in e for e in kit.check_session(s)[0]))

    def test_add_registers_manually_dropped_files(self):
        self.new()
        s = self.session()
        (s / "inputs/documents/lista.csv").write_text("a,b\n", encoding="utf-8")
        code, out = run("add", str(s))
        self.assertEqual(code, 0)
        self.assertIn("registered: inputs/documents/lista.csv", out)

    def test_inbox_then_move_into_session(self):
        code, out = run("inbox", "--client", "ProPES", "--clients-dir", str(self.tmp))
        self.assertEqual(code, 0)
        inbox = self.tmp / "propes" / "inbox"
        self.assertTrue(inbox.is_dir())
        shutil.copy(FIXTURES / "synthetic_transcript.md", inbox / "t.md")
        code, _ = run("new", "--client", "ProPES", "--date", "2026-11-10", "--clients-dir", str(self.tmp),
                      "--move", "--transcript", str(inbox / "t.md"))
        self.assertEqual(code, 0)
        self.assertFalse((inbox / "t.md").exists(), "inbox file should be moved")
        s = self.tmp / "propes" / "sessions" / "2026-11-10"
        self.assertTrue((s / "inputs/transcript/t.md").is_file())
        self.assertEqual(kit.check_session(s)[0], [])

    def test_clients_lists_names_sessions_and_inboxes(self):
        self.new()
        (self.tmp / "_inbox").mkdir()
        (self.tmp / "_inbox" / "nuevo.md").write_text("x", encoding="utf-8")
        code, out = run("clients", "--clients-dir", str(self.tmp))
        self.assertEqual(code, 0)
        self.assertIn("(1 pending: nuevo.md)", out)
        self.assertIn("panaderia-nandu-s-a-s: names=['Panadería Ñandú S.A.S.']; sessions=2026-11-10", out)
        self.assertNotIn("  _inbox:", out)

    def test_configured_rates(self):
        text = (kit.TEMPLATE.read_text(encoding="utf-8")
                .replace("developer: null", "developer: 90000")
                .replace("target_gross_margin: null", "target_gross_margin: 0.4  # 40%"))
        self.assertEqual(kit.configured_rates(text), ["developer", "target_gross_margin"])
        self.assertEqual(kit.configured_rates(kit.TEMPLATE.read_text(encoding="utf-8")), [])

    def test_check_outputs_ids_tags_and_leaks(self):
        self.new("--transcript", str(FIXTURES / "synthetic_transcript.md"))
        out = self.session() / "outputs"
        (out / "06_opportunities").mkdir(parents=True)
        for name in kit.REQUIRED_OUTPUTS:
            if name.endswith(".md"):
                (out / name).write_text("# x\n", encoding="utf-8")
        (out / "00_README.md").write_text("Cobertura: ...", encoding="utf-8")
        (out / "04_problem_register.md").write_text("P-001 [CONFIRMADO] TR:00:07:02", encoding="utf-8")
        (out / "05_opportunity_backlog.md").write_text("OPP-001 from P-001", encoding="utf-8")
        (out / "06_opportunities/OPP-001.md").write_text("P-001", encoding="utf-8")
        self.assertEqual(kit.check_session(self.session())[0], [])

        (out / "07_delivery_pricing.md").write_text("see P-009 and OPP-004", encoding="utf-8")
        (out / "10_client_followup_draft.md").write_text("margen bruto 40%\napi_key = abcdef123456", encoding="utf-8")
        errors, warnings, _ = kit.check_session(self.session())
        joined = "\n".join(errors)
        self.assertIn("P-009", joined)
        self.assertIn("OPP-004", joined)
        self.assertIn("credential", joined)
        self.assertTrue(any("internal-only" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
