"""Tests for scripts/analyze.py — using stdlib unittest."""
import sys
import os
import io
import contextlib
import json as _json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Import shim: lets us `import analyze` without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import analyze


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

def _write_skill(root, name, description, body="full body here"):
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )
    return d


def _assistant_line(ts, tool_name, tool_input=None):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": tool_name,
                    "input": tool_input or {},
                }
            ]
        },
    }


def _write_transcript(projects_dir, project, lines):
    d = projects_dir / project
    d.mkdir(parents=True)
    f = d / "session.jsonl"
    f.write_text("\n".join(_json.dumps(x) for x in lines))


def _item(type_, name, persistent):
    return {
        "type": type_,
        "name": name,
        "scope": "user",
        "persistent_tokens_est": persistent,
        "ondemand_tokens_est": 0,
        "cost_basis": "estimated",
        "source_path": "/x",
    }


# ---------------------------------------------------------------------------
# Task 2: estimate_tokens / parse_ts
# ---------------------------------------------------------------------------

class TestEstimateTokens(unittest.TestCase):

    def test_estimate_tokens_is_chars_over_four(self):
        self.assertEqual(analyze.estimate_tokens(""), 0)
        self.assertEqual(analyze.estimate_tokens("a" * 8), 2)

    def test_parse_ts_handles_z_suffix(self):
        dt = analyze.parse_ts("2026-05-01T10:03:59.727Z")
        self.assertEqual(dt, datetime(2026, 5, 1, 10, 3, 59, 727000, tzinfo=timezone.utc))

    def test_parse_ts_handles_none_and_garbage(self):
        self.assertIsNone(analyze.parse_ts(None))
        self.assertIsNone(analyze.parse_ts("not-a-date"))


# ---------------------------------------------------------------------------
# Task 3: read_frontmatter / collect_skills
# ---------------------------------------------------------------------------

class TestReadFrontmatter(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_read_frontmatter(self):
        f = self.tmp_path / "SKILL.md"
        f.write_text("---\nname: x\ndescription: hello world\n---\nBODY TEXT\n")
        desc, body = analyze.read_frontmatter(f)
        self.assertEqual(desc, "hello world")
        self.assertIn("BODY TEXT", body)

    def test_collect_skills_finds_user_and_project(self):
        home = self.tmp_path / "home"
        project = self.tmp_path / "proj"
        _write_skill(home, "alpha", "alpha desc")
        _write_skill(project, "beta", "beta desc")
        items = analyze.collect_skills(home, project)
        names = {i["name"]: i for i in items}
        self.assertEqual(set(names), {"alpha", "beta"})
        self.assertEqual(names["alpha"]["scope"], "user")
        self.assertEqual(names["beta"]["scope"], "project")
        self.assertEqual(names["alpha"]["type"], "skill")
        self.assertEqual(
            names["alpha"]["persistent_tokens_est"],
            analyze.estimate_tokens("alpha desc"),
        )
        self.assertGreater(names["alpha"]["ondemand_tokens_est"], 0)


# ---------------------------------------------------------------------------
# Task 4: collect_md_items / collect_memory
# ---------------------------------------------------------------------------

class TestCollectMdItemsAndMemory(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_collect_md_items_for_agents(self):
        agents = self.tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "reviewer.md").write_text(
            "---\ndescription: reviews code\n---\nlong prompt\n"
        )
        items = analyze.collect_md_items(agents, "subagent", "user")
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["name"], "reviewer")
        self.assertEqual(it["type"], "subagent")
        self.assertEqual(it["scope"], "user")
        self.assertEqual(
            it["persistent_tokens_est"], analyze.estimate_tokens("reviews code")
        )
        self.assertGreater(it["ondemand_tokens_est"], 0)

    def test_collect_memory_sizes_files(self):
        home = self.tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").write_text("x" * 400)
        items = analyze.collect_memory(home, self.tmp_path / "proj")
        mem = [i for i in items if i["name"] == "CLAUDE.md (user)"][0]
        self.assertEqual(mem["type"], "memory")
        self.assertEqual(mem["persistent_tokens_est"], 100)  # 400 chars / 4
        self.assertEqual(mem["ondemand_tokens_est"], 0)


# ---------------------------------------------------------------------------
# Task 5: collect_mcp_servers
# ---------------------------------------------------------------------------

class TestCollectMcpServers(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_collect_mcp_from_claude_json_and_project(self):
        home = self.tmp_path / "home"
        home.mkdir()
        (home / ".claude.json").write_text(
            _json.dumps(
                {
                    "mcpServers": {"global-srv": {"command": "x"}},
                    "projects": {
                        "/some/path": {"mcpServers": {"proj-scoped": {"command": "y"}}}
                    },
                }
            )
        )
        project = self.tmp_path / "proj"
        project.mkdir()
        (project / ".mcp.json").write_text(
            _json.dumps({"mcpServers": {"local-srv": {"command": "z"}}})
        )
        items = analyze.collect_mcp_servers(home, project)
        by_name = {i["name"]: i for i in items}
        self.assertEqual(set(by_name), {"global-srv", "proj-scoped", "local-srv"})
        self.assertEqual(by_name["global-srv"]["type"], "mcp")
        self.assertIsNone(by_name["global-srv"]["persistent_tokens_est"])
        self.assertEqual(by_name["global-srv"]["cost_basis"], "unknown-v1")
        self.assertEqual(by_name["local-srv"]["scope"], "project")


# ---------------------------------------------------------------------------
# Task 6: keys_for_tool / parse_usage
# ---------------------------------------------------------------------------

class TestKeysForToolAndParseUsage(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_keys_for_tool_attribution(self):
        self.assertEqual(
            analyze.keys_for_tool("mcp__github__create_issue", {}),
            [("mcp", "github")],
        )
        self.assertEqual(
            analyze.keys_for_tool("Skill", {"skill": "code-review"}),
            [("skill", "code-review")],
        )
        self.assertEqual(
            analyze.keys_for_tool("Task", {"subagent_type": "Explore"}),
            [("subagent", "Explore")],
        )
        self.assertEqual(analyze.keys_for_tool("Read", {}), [])

    def test_parse_usage_counts_window_and_last_used(self):
        projects = self.tmp_path / "projects"
        now = datetime(2026, 5, 28, tzinfo=timezone.utc)
        _write_transcript(
            projects,
            "-proj-a",
            [
                _assistant_line("2026-05-27T10:00:00Z", "mcp__github__x"),  # in window
                _assistant_line("2026-01-01T10:00:00Z", "mcp__github__x"),  # out of window
                _assistant_line(
                    "2026-05-20T10:00:00Z", "Skill", {"skill": "deep-research"}
                ),
            ],
        )
        usage, earliest, parse_warnings = analyze.parse_usage(projects, now)
        gh = usage[("mcp", "github")]
        self.assertEqual(gh["all"], 2)
        self.assertEqual(gh["30d"], 1)
        self.assertEqual(gh["last"], datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(gh["projects"], {"-proj-a"})
        self.assertEqual(usage[("skill", "deep-research")]["30d"], 1)
        self.assertEqual(earliest, datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(parse_warnings, 0)

    def test_parse_usage_counts_malformed_lines(self):
        """parse_usage returns parse_warnings count for malformed JSONL lines."""
        projects = self.tmp_path / "projects"
        proj_dir = projects / "-proj-b"
        proj_dir.mkdir(parents=True)
        # One malformed line + one valid tool_use line
        lines = [
            "{not json",
            _json.dumps(_assistant_line("2026-05-27T10:00:00Z", "mcp__github__x")),
        ]
        (proj_dir / "session.jsonl").write_text("\n".join(lines))
        now = datetime(2026, 5, 28, tzinfo=timezone.utc)
        usage, earliest, parse_warnings = analyze.parse_usage(projects, now)
        self.assertEqual(parse_warnings, 1)
        self.assertEqual(usage[("mcp", "github")]["all"], 1)


# ---------------------------------------------------------------------------
# Task 7: usage_key_for_item, merge_usage, build_output
# ---------------------------------------------------------------------------

class TestMergeUsageAndBuildOutput(unittest.TestCase):

    def test_merge_usage_and_unused_flagging(self):
        items = [_item("skill", "used-skill", 100), _item("skill", "cold-skill", 50)]
        now = datetime(2026, 5, 28, tzinfo=timezone.utc)
        usage = {
            ("skill", "used-skill"): {
                "all": 5,
                "30d": 5,
                "last": datetime(2026, 5, 27, tzinfo=timezone.utc),
                "projects": {"-a", "-b"},
            }
        }
        analyze.merge_usage(items, usage)
        used = [i for i in items if i["name"] == "used-skill"][0]
        cold = [i for i in items if i["name"] == "cold-skill"][0]
        self.assertEqual(used["invocations_30d"], 5)
        self.assertEqual(sorted(used["projects_used"]), ["-a", "-b"])
        self.assertEqual(used["last_used"], "2026-05-27T00:00:00+00:00")
        self.assertEqual(cold["invocations_30d"], 0)
        self.assertIsNone(cold["last_used"])

    def test_build_output_totals(self):
        items = [
            _item("skill", "used", 100),
            _item("skill", "cold", 50),
            _item("memory", "CLAUDE.md (user)", 300),
            _item("mcp", "srv", None),
        ]
        items[3]["persistent_tokens_est"] = None
        now = datetime(2026, 5, 28, tzinfo=timezone.utc)
        usage = {
            ("skill", "used"): {"all": 1, "30d": 1, "last": now, "projects": {"-a"}},
            ("mcp", "srv"): {"all": 0, "30d": 0, "last": None, "projects": set()},
        }
        analyze.merge_usage(items, usage)
        out = analyze.build_output(
            items, datetime(2026, 4, 1, tzinfo=timezone.utc), now
        )
        self.assertEqual(out["totals"]["context_tax_est"], 450)   # 100+50+300 (mcp None excluded)
        self.assertEqual(out["totals"]["reclaimable_est"], 50)    # only the cold skill
        self.assertEqual(out["totals"]["unused_mcp_count"], 1)
        self.assertEqual(out["totals"]["history_horizon_days"], 57)
        # sorted: biggest persistent first → memory(300) comes first
        self.assertEqual(out["items"][0]["persistent_tokens_est"], 300)


# ---------------------------------------------------------------------------
# Task 8: run_audit end-to-end / CLI
# ---------------------------------------------------------------------------

class TestRunAudit(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_run_audit_end_to_end(self):
        home = self.tmp_path / "home"
        project = self.tmp_path / "proj"
        _write_skill(home, "cold", "an unused skill")
        projects = home / ".claude" / "projects"
        _write_transcript(
            projects,
            "-proj",
            [
                _assistant_line("2026-05-27T10:00:00Z", "Skill", {"skill": "cold"}),
            ],
        )
        now = datetime(2026, 5, 28, tzinfo=timezone.utc)
        out = analyze.run_audit(home, project, now)
        self.assertTrue(out["items"])
        cold = [i for i in out["items"] if i["name"] == "cold"][0]
        self.assertEqual(cold["invocations_30d"], 1)


# ---------------------------------------------------------------------------
# Task 9: disable_item / undo_item
# ---------------------------------------------------------------------------

class TestDisableAndUndo(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_disable_and_undo_skill(self):
        home = self.tmp_path / "home"
        project = self.tmp_path / "proj"
        skill_dir = _write_skill(home, "victim", "to disable")
        disabled = home / ".claude" / "ci-disabled"
        res = analyze.disable_item("skill", "victim", home, project, disabled)
        self.assertFalse(skill_dir.exists())
        self.assertTrue((disabled / "skill" / "victim" / "SKILL.md").exists())
        self.assertIn("undo", res)
        analyze.undo_item("skill", "victim", home, project, disabled)
        self.assertTrue(skill_dir.exists())
        self.assertTrue((skill_dir / "SKILL.md").read_text().startswith("---"))

    def test_disable_mcp_backs_up_and_removes(self):
        home = self.tmp_path / "home"
        project = self.tmp_path / "proj"
        cj = home / ".claude.json"
        home.mkdir()
        cj.write_text(
            _json.dumps(
                {
                    "mcpServers": {
                        "victim": {"command": "x"},
                        "keep": {"command": "y"},
                    }
                }
            )
        )
        disabled = home / ".claude" / "ci-disabled"
        analyze.disable_item("mcp", "victim", home, project, disabled)
        data = _json.loads(cj.read_text())
        self.assertNotIn("victim", data["mcpServers"])
        self.assertIn("keep", data["mcpServers"])
        self.assertTrue(list(disabled.glob("*.bak")))  # a backup was written
        analyze.undo_item("mcp", "victim", home, project, disabled)
        self.assertIn("victim", _json.loads(cj.read_text())["mcpServers"])


# ---------------------------------------------------------------------------
# Fix 1: collect_plugin_skills
# ---------------------------------------------------------------------------

class TestCollectPluginSkills(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _write_plugin_skill(self, home, market, plugin, version, skill_name, description, body="plugin body"):
        skill_dir = (
            home / ".claude" / "plugins" / market / plugin / version / "skills" / skill_name
        )
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\ndescription: {description}\n---\n{body}\n"
        )
        return skill_dir

    def test_collect_plugin_skills_returns_one_item(self):
        home = self.tmp_path / "home"
        self._write_plugin_skill(
            home, "somemarket", "myplugin", "1.0.0", "coolskill", "a cool plugin skill"
        )
        items = analyze.collect_plugin_skills(home)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["name"], "coolskill")
        self.assertEqual(it["scope"], "plugin")
        self.assertEqual(it["type"], "skill")
        self.assertGreater(it["persistent_tokens_est"], 0)
        self.assertEqual(it["cost_basis"], "estimated")

    def test_collect_plugin_skills_no_plugins_dir(self):
        home = self.tmp_path / "home"
        home.mkdir()
        items = analyze.collect_plugin_skills(home)
        self.assertEqual(items, [])

    def test_collect_plugin_skills_dedup_by_source_path(self):
        """Two SKILL.md files in the same skill dir should produce one item."""
        home = self.tmp_path / "home"
        # Write two different plugin skills
        self._write_plugin_skill(home, "m", "p", "1.0", "skill-a", "desc a")
        self._write_plugin_skill(home, "m", "p", "1.0", "skill-b", "desc b")
        items = analyze.collect_plugin_skills(home)
        self.assertEqual(len(items), 2)
        paths = {i["source_path"] for i in items}
        self.assertEqual(len(paths), 2)

    def test_run_audit_includes_plugin_skills(self):
        home = self.tmp_path / "home"
        project = self.tmp_path / "proj"
        self._write_plugin_skill(
            home, "somemarket", "myplugin", "1.0.0", "coolskill", "a cool plugin skill"
        )
        now = datetime(2026, 5, 28, tzinfo=timezone.utc)
        out = analyze.run_audit(home, project, now)
        plugin_items = [i for i in out["items"] if i.get("scope") == "plugin"]
        self.assertEqual(len(plugin_items), 1)
        self.assertEqual(plugin_items[0]["name"], "coolskill")


# ---------------------------------------------------------------------------
# Fix 2: disable_item / undo_item for project-scoped & .mcp.json MCP servers
# ---------------------------------------------------------------------------

class TestDisableMcpProjectScoped(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_disable_undo_mcp_in_claude_json_projects_key(self):
        """disable_item finds and removes a server under projects[path].mcpServers."""
        home = self.tmp_path / "home"
        project = self.tmp_path / "proj"
        home.mkdir()
        project.mkdir()
        cj = home / ".claude.json"
        cj.write_text(_json.dumps({
            "mcpServers": {"global-srv": {"command": "g"}},
            "projects": {
                "/some/path": {
                    "mcpServers": {
                        "proj-victim": {"command": "p"},
                        "proj-keep": {"command": "k"},
                    }
                }
            },
        }))
        disabled = home / ".claude" / "ci-disabled"

        # Disable
        res = analyze.disable_item("mcp", "proj-victim", home, project, disabled)
        self.assertTrue(res["ok"], res)
        data = _json.loads(cj.read_text())
        self.assertNotIn("proj-victim", data["projects"]["/some/path"]["mcpServers"])
        self.assertIn("proj-keep", data["projects"]["/some/path"]["mcpServers"])
        self.assertIn("global-srv", data["mcpServers"])

        # Undo
        res2 = analyze.undo_item("mcp", "proj-victim", home, project, disabled)
        self.assertTrue(res2["ok"], res2)
        data2 = _json.loads(cj.read_text())
        self.assertIn("proj-victim", data2["projects"]["/some/path"]["mcpServers"])

    def test_disable_undo_mcp_in_dot_mcp_json(self):
        """disable_item finds and removes a server in project/.mcp.json."""
        home = self.tmp_path / "home"
        project = self.tmp_path / "proj"
        home.mkdir()
        project.mkdir()
        cj = home / ".claude.json"
        cj.write_text(_json.dumps({"mcpServers": {}}))
        mcp_json = project / ".mcp.json"
        mcp_json.write_text(_json.dumps({
            "mcpServers": {
                "local-victim": {"command": "lv"},
                "local-keep": {"command": "lk"},
            }
        }))
        disabled = home / ".claude" / "ci-disabled"

        # Disable
        res = analyze.disable_item("mcp", "local-victim", home, project, disabled)
        self.assertTrue(res["ok"], res)
        mcp_data = _json.loads(mcp_json.read_text())
        self.assertNotIn("local-victim", mcp_data["mcpServers"])
        self.assertIn("local-keep", mcp_data["mcpServers"])

        # Undo
        res2 = analyze.undo_item("mcp", "local-victim", home, project, disabled)
        self.assertTrue(res2["ok"], res2)
        mcp_data2 = _json.loads(mcp_json.read_text())
        self.assertIn("local-victim", mcp_data2["mcpServers"])


if __name__ == "__main__":
    unittest.main()
